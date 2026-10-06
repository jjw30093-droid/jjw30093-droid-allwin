// 一次模拟结果的可序列化快照:结果页、分享图、分享链接都只读它。
// 打开分享链接时直接展示链接里的快照,不依赖当前参数重新计算,保证"原样展示这次结果"。

import { SIMULATOR_ENGINE_VERSION, type Focus, type ManyResult, type MatchConfig, type MatchSetup, type SingleResult } from "./engine";
import { withShotDetails } from "./shotDetail";
import type { PosGroup, SimParams } from "./types";

export interface SnapLineupEntry {
  positionId: number;
  group: PosGroup;
  playerId: string | null;
  name: string;
  num: string | null;
  x: number;
  y: number;
}

export interface SnapTeam {
  teamId: number;
  name: string;
  formation: string;
  focuses: Focus[];
  shortRest: boolean;
  lineup: SnapLineupEntry[];
  expectedGoals: number;
}

export interface ResultSnapshot {
  sv: 1;
  modelVersion: string;
  paramsDate: string;
  leagueId: number;
  seed: number;
  chaos: boolean;
  kappa: number;
  fixtureId: number | null;
  market: { status: string; finalScore: [number, number] | null } | null;
  teams: [SnapTeam, SnapTeam];
  single: SingleResult;
  many: ManyResult;
}

const r = (x: number, d: number) => Math.round(x * 10 ** d) / 10 ** d;

export function modelVersionOf(params: SimParams): string {
  // 保留参数入参：调用方用同一接口同时检查“当前参数日期”和“当前剧情引擎版本”。
  void params;
  return SIMULATOR_ENGINE_VERSION;
}

/** 快照保留全部事件(门将失误机会的 xG 计入累计 xG,伤病进事件流),xG 保留 3 位小数。 */
export function makeSnapshot(params: SimParams, setup: MatchSetup, config: MatchConfig, single: SingleResult, many: ManyResult): ResultSnapshot {
  const team = (t: 0 | 1): SnapTeam => {
    const s = t === 0 ? setup.home : setup.away;
    return {
      teamId: s.teamId,
      name: config.teams[t].name,
      formation: s.formation,
      focuses: [...s.focuses],
      shortRest: s.shortRest,
      lineup: s.slots.map((sl) => {
        const p = sl.playerId ? params.players[sl.playerId] : undefined;
        return {
          positionId: sl.positionId,
          group: sl.group,
          playerId: sl.playerId,
          name: p ? p.name_zh || p.name_en || "未知球员" : "空位",
          num: p?.shirt_number ?? null,
          x: r(sl.x, 3),
          y: r(sl.y, 3),
        };
      }),
      expectedGoals: r(config.teams[t].breakdown.expectedGoals, 4),
    };
  };
  return {
    sv: 1,
    modelVersion: modelVersionOf(params),
    paramsDate: params.meta.generated_at,
    leagueId: setup.leagueId,
    seed: single.seed,
    chaos: setup.chaos,
    kappa: config.kappa,
    fixtureId: setup.fixtureId,
    market: config.market ? { status: config.market.status, finalScore: config.market.finalScore } : null,
    teams: [team(0), team(1)],
    single: {
      ...single,
      epsilon: [r(single.epsilon[0], 4), r(single.epsilon[1], 4)],
      // 射门细节(真实射门位置/结果,供动画)在这里抽好写进快照;分享链接打开时原样使用。
      // 先把 xG 取整再抽:与旧链接补细节(ensureShotDetails,只能看到取整后的 xG)输入完全相同
      events: withShotDetails(
        single.events.map((e) => (e.xg === undefined ? e : { ...e, xg: r(e.xg, 3) })),
        single.seed,
        params.shot_samples,
      ),
    },
    many: {
      ...many,
      pHome: r(many.pHome, 4),
      pDraw: r(many.pDraw, 4),
      pAway: r(many.pAway, 4),
      meanGoals: [r(many.meanGoals[0], 3), r(many.meanGoals[1], 3)],
      topScores: many.topScores.map((s) => ({ score: s.score, p: r(s.p, 4) })),
      scorerProb: many.scorerProb.map((s) => ({ ...s, p: r(s.p, 4) })),
      upset: { ...many.upset, outcomeShare: r(many.upset.outcomeShare, 4) },
      pBigMargin: r(many.pBigMargin, 4),
      meanReds: r(many.meanReds, 4),
      meanPenalties: r(many.meanPenalties, 4),
      goalBuckets: many.goalBuckets.map((x) => r(x, 4)),
      crown: many.crown
        ? {
            ahLine: many.crown.ahLine,
            ouLine: many.crown.ouLine,
            pEffAhHome: many.crown.pEffAhHome == null ? null : r(many.crown.pEffAhHome, 4),
            pEffOver: many.crown.pEffOver == null ? null : r(many.crown.pEffOver, 4),
          }
        : null,
    },
  };
}

// 库内口径:line > 0 = 主让(research/ah_signals/common.py 顶部注释)。
export function ahText(line: number): string {
  if (Math.abs(line) < 1e-9) return "平手";
  return line > 0 ? `主队让 ${line}` : `主队受让 ${Math.abs(line)}`;
}

/** 随机强度档位(面向用户):标准 / 混乱模式 */
export function kappaUserLabel(chaos: boolean): string {
  return chaos ? "混乱模式(更随机)" : "标准";
}

/** 旧分享链接(射门细节上线前生成的)里没有射门细节:用当前参数的样本库按同一规则补上(确定性)。 */
export function ensureShotDetails(snap: ResultSnapshot, params: SimParams): ResultSnapshot {
  if (snap.single.events.every((e) => e.sd || (e.kind !== "shot" && e.kind !== "goal"))) return snap;
  return { ...snap, single: { ...snap.single, events: withShotDetails(snap.single.events, snap.seed, params.shot_samples) } };
}
