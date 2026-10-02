// 模拟器参数的服务端读取(只由 page.tsx 这个服务端组件引用;用到 node:fs,不得被客户端组件 import)。
// 口径:docs/simulator-launch-plan.md §3.2。
//
// - 生产:SIMULATOR_PARAMS_DIR 下的 current.json(每日任务发布的软链);读不到或校验不过时按文件名日期从新到旧
//   尝试 simulator_params_*.json;全部失败返回 null,页面显示"参数暂不可用",不抛异常。
// - 开发:SIMULATOR_PARAMS_PATH 指向单个文件。
// - 进程内按(真实路径, mtime)缓存解析结果,current.json 换新后下一次请求自动换新。

import { readdir, readFile, realpath, stat } from "node:fs/promises";
import { join } from "node:path";
import type { FixtureParams, SimParams } from "@/features/simulator/types";

export const CALIBRATED_LEAGUES = [47, 87, 55, 54, 53] as const;
export const DEFAULT_LEAGUE = 47;

const cache = new Map<string, { mtimeMs: number; params: SimParams }>();

function looksValid(p: SimParams): boolean {
  return (
    !!p?.meta?.generated_at &&
    !!p.leagues &&
    CALIBRATED_LEAGUES.some((l) => p.leagues[String(l)]) &&
    !!p.teams &&
    Object.keys(p.teams).length > 0 &&
    !!p.players &&
    !!p.position_map
  );
}

async function loadFile(path: string): Promise<SimParams | null> {
  try {
    const real = await realpath(path);
    const { mtimeMs } = await stat(real);
    const hit = cache.get(real);
    if (hit && hit.mtimeMs === mtimeMs) return hit.params;
    const params = JSON.parse(await readFile(real, "utf8")) as SimParams;
    if (!looksValid(params)) {
      console.error(`[simulator] 参数文件校验未通过:${real}`);
      return null;
    }
    cache.set(real, { mtimeMs, params });
    return params;
  } catch (e) {
    console.error(`[simulator] 读取参数失败:${path}:${e instanceof Error ? e.message : String(e)}`);
    return null;
  }
}

export interface LoadedParams {
  params: SimParams;
  source: string;
  fallback: boolean;
}

export async function loadParams(env: { dir?: string; path?: string }): Promise<LoadedParams | null> {
  if (env.path) {
    const p = await loadFile(env.path);
    return p ? { params: p, source: env.path, fallback: false } : null;
  }
  if (!env.dir) return null;
  const current = await loadFile(join(env.dir, "current.json"));
  if (current) return { params: current, source: "current.json", fallback: false };
  let names: string[] = [];
  try {
    names = (await readdir(env.dir)).filter((n) => /^simulator_params_\d{8}\.json$/.test(n)).sort().reverse();
  } catch (e) {
    console.error(`[simulator] 无法列出参数目录:${env.dir}:${e instanceof Error ? e.message : String(e)}`);
  }
  for (const n of names) {
    const p = await loadFile(join(env.dir, n));
    if (p) {
      console.error(`[simulator] current.json 不可用,回退到 ${n}`);
      return { params: p, source: n, fallback: true };
    }
  }
  return null;
}

export function leagueOf(lg: string | string[] | undefined, params: SimParams): number {
  const n = Number(Array.isArray(lg) ? lg[0] : lg);
  const available = CALIBRATED_LEAGUES.filter((l) => params.leagues[String(l)]);
  return available.includes(n as (typeof CALIBRATED_LEAGUES)[number]) ? n : (available.includes(DEFAULT_LEAGUE) ? DEFAULT_LEAGUE : available[0]);
}

/** 只把当前联赛的球队、球员、赛程下发给客户端;联赛级参数、阵型模板、位置映射、校准与 meta 全部保留。 */
export function sliceForLeague(params: SimParams, leagueId: number): SimParams {
  const teams = Object.fromEntries(Object.entries(params.teams).filter(([, t]) => t.league_id === leagueId));
  const ids = new Set<string>();
  for (const t of Object.values(teams)) {
    t.squad.forEach((id) => ids.add(id));
    t.last_lineup?.starters.forEach((s) => ids.add(s.player_id));
  }
  const players = Object.fromEntries(Object.entries(params.players).filter(([id]) => ids.has(id)));
  const fixtures = Object.fromEntries(Object.entries(params.fixtures).filter(([, f]) => f.league_id === leagueId));
  const leagues = Object.fromEntries(
    Object.entries(params.leagues).filter(([id]) => CALIBRATED_LEAGUES.includes(Number(id) as (typeof CALIBRATED_LEAGUES)[number])),
  );
  return { ...params, leagues, teams, players, fixtures };
}

export interface FixtureIndexEntry {
  match_id: number;
  league_id: number;
  home_team_id: number;
  away_team_id: number;
  home_name: string;
  away_name: string;
  kickoff_at_utc: string;
  status: string;
  ah_line: number | null;
  ou_line: number | null;
  final_score: [number, number] | null;
}

/** 五个联赛合并的"有 Crown 盘口的真实比赛"小索引(只含列表需要的字段)。
 *  `leagues` 给定时只含这些联赛——页面服务端只能给匿名口径(英超),盘口线本身就是
 *  赔率数据;登录用户的全联赛索引经 /api/v1/simulator/fixtures 下发
 *  (Python 对应实现:backend/queries/simulator_params.py,两边同步改)。 */
export function fixtureIndex(params: SimParams, leagues?: ReadonlySet<number>): FixtureIndexEntry[] {
  const name = (id: number) => params.teams[String(id)]?.name_zh ?? String(id);
  return Object.values(params.fixtures)
    .filter((f: FixtureParams) => f.market_lambda && CALIBRATED_LEAGUES.includes(f.league_id as (typeof CALIBRATED_LEAGUES)[number]))
    .filter((f: FixtureParams) => !leagues || leagues.has(f.league_id))
    .map((f) => ({
      match_id: f.match_id,
      league_id: f.league_id,
      home_team_id: f.home_team_id,
      away_team_id: f.away_team_id,
      home_name: name(f.home_team_id),
      away_name: name(f.away_team_id),
      kickoff_at_utc: f.kickoff_at_utc,
      status: f.status,
      ah_line: f.ah?.line ?? null,
      ou_line: f.ou?.line ?? null,
      final_score: f.final_score,
    }));
}

export { isStale } from "@/features/simulator/staleness";
