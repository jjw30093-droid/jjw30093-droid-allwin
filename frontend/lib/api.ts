// 前端只读 serving API,不直连 DB(CLAUDE.md §2)。基址统一来自 lib/api-base.ts
// (单一真源,宪法 §10.3),本文件不再自行解析 env。
// 本文件的 fetcher 只在 Server Component(旧 /league 页面)里调用,因此用服务端
// 基址(INTERNAL_API_BASE > NEXT_PUBLIC_API_BASE > 127.0.0.1:8000)。
//
// 类型全部从生成的 lib/api-types.ts 派生(Pydantic 单一真源,backend/api/schemas.py
// 的 Legacy* DTO;npm run gen:api 重新生成),不再手写与 API 响应重复的 interface
// (CLAUDE.md §10.3)。旧手写类型曾对同一来源列做出不一致的可空性假设(如
// MatchRow.Match_Round 声明为非空,而 wdl-predictions 的同源字段声明可空)——
// 生成类型按 backend/api_server.py 真实返回值精确建模,消除了这一不一致。
import { serverApiBase } from "./api-base";
import type { GetJson } from "./api-v1";

function seasonQuery(season?: string): string {
  return season ? `?season=${encodeURIComponent(season)}` : "";
}

export type LeagueOverview = GetJson<"/api/league/{league_id}/overview">;
export type StandingRow = LeagueOverview["standings"][number];
// 免费字段(CLAUDE.md §3):射门/射正/控球/xG/xGOT。角球/黄红牌/零封/BTTS
// 是付费字段,不在 overview 里返回,这里也就没有对应字段——不是漏写。
export type TeamStatsRow = LeagueOverview["team_stats"][number];
export type PlayerLeaderboard = LeagueOverview["player_leaderboards"][string];
export type PlayerLeaderboardEntry = PlayerLeaderboard["entries"][number];

export async function fetchLeagueOverview(
  leagueId: string,
  season?: string
): Promise<LeagueOverview> {
  const url = `${serverApiBase()}/api/league/${leagueId}/overview${seasonQuery(season)}`;

  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`serving API ${res.status}: ${body}`);
  }
  return res.json();
}

// 赛程/结果(免费层)。只有对阵/比分/日期/轮次/status,不含任何概率字段
// ——概率是付费概率卡页(wdl-predictions)的事,这里没有、也不该有。
export type LeagueMatchesResponse = GetJson<"/api/league/{league_id}/matches">;
export type MatchRow = LeagueMatchesResponse["matches"][number];

export async function fetchLeagueMatches(
  leagueId: string,
  season?: string
): Promise<LeagueMatchesResponse> {
  const url = `${serverApiBase()}/api/league/${leagueId}/matches${seasonQuery(season)}`;

  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`serving API ${res.status}: ${body}`);
  }
  return res.json();
}

// WDL 概率卡(付费核心,CLAUDE.md §3)。三种 JSON 形状物理上互斥(后端
// LegacyWdlUpcomingMatch / LiveLockedMatch / LiveFullMatch 判别联合,各自
// extra=forbid),不是同一个宽松 dict 里若干字段可选/可空:
//   1. availability='upcoming'(距开赛 >7 天):物理上没有 tendency/confidence/
//      reason/locked/p_home/p_draw/p_away 这些字段——时候没到,谁都看不到。
//   2. availability='live' 且 locked=true(未付费):有 tendency/confidence/
//      reason,物理上没有 p_home/p_draw/p_away。
//   3. availability='live' 且 locked=false(已付费):额外带完整三项概率。
// 因此消费方必须先按 availability 收窄,再按 locked 收窄,TypeScript 才允许
// 访问对应字段——这是编译期强制,不是约定俗成。
export type WdlPredictionsResponse = GetJson<"/api/league/{league_id}/wdl-predictions">;
export type WdlMatch = WdlPredictionsResponse["matches"][number];
export type WdlLiveMatch = Extract<WdlMatch, { availability: "live" }>;
export type Tendency = NonNullable<WdlLiveMatch["tendency"]>;
export type Confidence = NonNullable<WdlLiveMatch["confidence"]>;
export type Availability = WdlMatch["availability"];

export async function fetchWdlPredictions(
  leagueId: string,
  season?: string
): Promise<WdlPredictionsResponse> {
  const url = `${serverApiBase()}/api/league/${leagueId}/wdl-predictions${seasonQuery(season)}`;

  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`serving API ${res.status}: ${body}`);
  }
  return res.json();
}
