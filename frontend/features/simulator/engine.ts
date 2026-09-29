// 比赛模拟引擎(纯函数,可在 Web Worker 与测试里直接运行)。
// 口径:docs/simulator-model.md v0 + v0.1 + v0.2。下面所有数值常量都是规格里的 [待校准] 初始假设。

import { fairLine, pEff, type IntDist } from "./market";
import { gamma, mulberry32, poisson, weightedPick, type Rng } from "./rng";
import {
  SHOT_CHANNELS,
  type Channel,
  type FixtureParams,
  type LeagueParams,
  type PlayerParams,
  type PosGroup,
  type RateModel,
  type ShotChannel,
  type SimParams,
  type StateMultipliers,
} from "./types";

const HA_HOME = 1.08;
const HA_AWAY = 0.93;
const MARKET_W = 0.7;
const R_ATT_CLAMP: [number, number] = [0.85, 1.15];
const M_DEF_SLOPE = 0.08;
const M_DEF_UNIT = 0.3;
const M_DEF_CLAMP: [number, number] = [0.9, 1.1];
const M_GK_COEF = 0.5;
const M_GK_CLAMP: [number, number] = [0.9, 1.1];
const FOCUS_OWN_CAP: [number, number] = [0.85, 1.15];
const FOCUS_OPP_CAP: [number, number] = [0.88, 1.12];
const REST_OWN = 0.96;
const REST_OPP = 1.02;
const KAPPA_STANDARD = 12;
const KAPPA_CHAOS = 4;
const RED_OWN = 0.75;
const RED_OPP = 1.25;
export const V02_STATE: StateMultipliers = { trail1_open: 1.12, trail1_counter: 1.1, lead1_all: 0.92, trail2_all: 1.15, lead2_all: 0.9 };
const COLLAPSE_MULT = 1.15;
const COLLAPSE_WINDOW = 10;
const GK_ERROR_P = 0.02;
const GK_ERROR_XG = 0.5;
const INJURY_P = 0.1;
const INJURY_MULT = 0.98;
const PRESS_LATE_MULT = 0.93;
const PRESS_LATE_FROM = 70;
const HEADER_BOOST_CROSSING = 1.5;
// 没有任何出场数据的球员(射门/90 为空)的射手权重底数——实现兜底,不是规格参数。
const SHOTS90_FALLBACK = 0.3;
const DEFENSIVE_GROUPS: ReadonlySet<PosGroup> = new Set(["GK", "CB", "FB", "DM"]);

const clamp = (x: number, [lo, hi]: [number, number]) => Math.min(hi, Math.max(lo, x));

// ------------------------------------------------------------------ 位置兼容(§4.3)
const ADJACENT: [PosGroup, PosGroup][] = [
  ["CB", "FB"], ["CB", "DM"], ["FB", "W"], ["DM", "CM"], ["CM", "AM"], ["AM", "W"], ["AM", "ST"], ["W", "ST"],
];

export function fPos(main: PosGroup, slot: PosGroup): number {
  if (main === slot) return 1;
  if ((main === "GK") !== (slot === "GK")) return 0.3;
  if (ADJACENT.some(([a, b]) => (a === main && b === slot) || (a === slot && b === main))) return 0.9;
  return 0.7;
}

/** 单一映射表 (阵型, position_id) → 8 组(规格 v0.2 第 5 条);模板槽位与参照阵容都只查这里。 */
export function slotGroup(params: SimParams, formation: string, positionId: number): PosGroup | null {
  return params.position_map[formation]?.[String(positionId)] ?? null;
}

// ------------------------------------------------------------------ 侧重点(§5)
export type Focus = "setpiece" | "counter" | "possession" | "press" | "crossing" | "lowblock";
export const FOCUS_LIST: Focus[] = ["setpiece", "counter", "possession", "press", "crossing", "lowblock"];
export const FOCUS_LABEL: Record<Focus, string> = {
  setpiece: "定位球",
  counter: "快速反击",
  possession: "控球渗透",
  press: "高位逼抢",
  crossing: "边路传中",
  lowblock: "稳守反击",
};
const EXCLUSIVE: [Focus, Focus][] = [["possession", "counter"], ["press", "lowblock"]];

export function focusError(focuses: Focus[]): string | null {
  if (focuses.length > 2) return "每队最多选 2 个侧重点";
  for (const [a, b] of EXCLUSIVE) {
    if (focuses.includes(a) && focuses.includes(b)) return `${FOCUS_LABEL[a]}与${FOCUS_LABEL[b]}不能同时选`;
  }
  return null;
}

// ------------------------------------------------------------------ 配置
export interface SlotAssign {
  positionId: number;
  group: PosGroup;
  playerId: string | null;
  x: number;
  y: number;
}

export interface TeamSetup {
  teamId: number;
  formation: string;
  slots: SlotAssign[];
  focuses: Focus[];
  shortRest: boolean;
}

/** 场内随机性开关。全部关闭时模拟进球期望等于输入 λ(规格 v0.2 第 1 条的验证口径)。 */
export interface Effects {
  epsilon: boolean;
  scoreState: boolean;
  redCard: boolean;
  collapse: boolean;
  gkError: boolean;
  injury: boolean;
}

export const ALL_EFFECTS: Effects = { epsilon: true, scoreState: true, redCard: true, collapse: true, gkError: true, injury: true };
export const NO_EFFECTS: Effects = { epsilon: false, scoreState: false, redCard: false, collapse: false, gkError: false, injury: false };

export interface MatchSetup {
  leagueId: number;
  home: TeamSetup;
  away: TeamSetup;
  fixtureId: number | null;
  chaos: boolean;
  effects?: Effects;
}

export interface SimPlayer {
  id: string;
  name: string;
  slotGroup: PosGroup;
  fPos: number;
  shots90: number;
  headers90: number;
  npxg90: number;
  pensTaken: number;
}

export interface TeamBreakdown {
  lambdaModel: number;
  lambdaMarket: number | null;
  lambdaBase: number;
  rAtt: number;
  mDef: number;
  mGk: number;
  focusOwnRatio: number;
  focusOppRatio: number;
  /** 射门渠道 λ 之和 + 乌龙 */
  lambdaFinal: number;
  /** 期望进球:射门渠道 λ × 对方门将 m_gk + 乌龙(模拟进球的期望值) */
  expectedGoals: number;
  lambdaByChannel: Record<Channel, number>;
}

export interface TeamConfig {
  teamId: number;
  name: string;
  lambda: Record<Channel, number>;
  shotCountMult: Record<ShotChannel, number>;
  xgMult: Record<ShotChannel, number>;
  pressLate: boolean;
  headerBoost: number;
  mGk: number;
  players: SimPlayer[];
  breakdown: TeamBreakdown;
}

export interface MatchConfig {
  kappa: number;
  effects: Effects;
  redOwn: number;
  redOpp: number;
  state: StateMultipliers;
  /** v0.3 回归系数;null 时按 v0.2 的 timing 归一化与乘数运行 */
  rateModel: RateModel | null;
  timing: number[];
  /** 补时经验分布:[分钟数[], 累计概率[]],上下半场各一份 */
  stoppageDist: [[number[], number[]], [number[], number[]]];
  xgQuantiles: Record<ShotChannel, number[]>;
  xgMean: Record<ShotChannel, number>;
  xgScale: Record<ShotChannel, number>;
  penaltyConversion: number;
  redCardRate: number;
  teams: [TeamConfig, TeamConfig];
  market: {
    fixtureId: number;
    status: string;
    ahLine: number | null;
    ouLine: number | null;
    finalScore: [number, number] | null;
  } | null;
}

export type PrepareResult = { ok: true; config: MatchConfig } | { ok: false; error: string };

function playerName(p: PlayerParams | undefined, id: string): string {
  return p?.name_zh || p?.name_en || id;
}

function interpMean(q: number[]): number {
  let s = 0;
  for (let i = 0; i + 1 < q.length; i++) s += (q[i] + q[i + 1]) / 2;
  return s / (q.length - 1);
}

export function matchingFixture(params: SimParams, homeId: number, awayId: number): FixtureParams[] {
  return Object.values(params.fixtures).filter(
    (f) => f.home_team_id === homeId && f.away_team_id === awayId && f.market_lambda,
  );
}

export function prepareMatch(params: SimParams, setup: MatchSetup): PrepareResult {
  const league: LeagueParams | undefined = params.leagues[String(setup.leagueId)];
  const sides = [setup.home, setup.away] as const;
  const teamP = sides.map((s) => params.teams[String(s.teamId)]);
  if (!league || !teamP[0] || !teamP[1]) return { ok: false, error: "缺少联赛或球队参数" };
  if (setup.home.teamId === setup.away.teamId) return { ok: false, error: "主客队不能相同" };

  for (let t = 0; t < 2; t++) {
    const s = sides[t];
    const name = teamP[t].name_zh ?? String(s.teamId);
    if (s.slots.some((x) => !x.playerId)) return { ok: false, error: `${name} 首发未排满 11 人` };
    const ids = s.slots.map((x) => x.playerId as string);
    if (new Set(ids).size !== ids.length) return { ok: false, error: `${name} 首发里有重复球员` };
    if (!ids.some((id) => params.players[id]?.main_position === "GK")) {
      return { ok: false, error: `${name} 首发中没有门将,不能模拟` };
    }
    const fe = focusError(s.focuses);
    if (fe) return { ok: false, error: `${name}:${fe}` };
    const ll = teamP[t].last_lineup;
    if (ll && ll.starters.some((r) => !slotGroup(params, ll.formation, r.position_id))) {
      return { ok: false, error: `${name} 参照阵型 ${ll.formation} 不在位置映射表中` };
    }
  }

  const mu = league.mu;
  const cal = params.calibration ?? {};
  const h = league.home_advantage;
  const ha = h ? [h, 1 / h] : [HA_HOME, HA_AWAY];
  const marketW = cal.market_w ?? MARKET_W;
  // §2 数据模型
  const lamModel = [0, 1].map((t) => {
    const o = 1 - t;
    const out = {} as Record<ShotChannel, number>;
    for (const c of SHOT_CHANNELS) {
      out[c] = mu[c] * (teamP[t].A[c] / mu[c]) * (teamP[o].D[c] / mu[c]) * ha[t];
    }
    return out;
  });
  const sumCh = (r: Record<ShotChannel, number>) => SHOT_CHANNELS.reduce((s, c) => s + r[c], 0);

  // v0.3 第 7 条:有强度回归时,λ_model 的总量取回归预测(不再乘主场系数),非乌龙部分按原公式的渠道占比分配
  const sm = cal.strength_model;
  if (sm && teamP.every((t) => t.strength?.[sm.window])) {
    const ki = sm.k === 0 ? 0 : 1;
    const muT = { xg: SHOT_CHANNELS.reduce((s, c) => s + mu[c], 0), goals: league.goals_per_team_match ?? 0 };
    for (let t = 0; t < 2; t++) {
      const o = 1 - t;
      const own = teamP[t].strength![sm.window];
      const opp = teamP[o].strength![sm.window];
      let eta = sm.alpha + (t === 0 ? sm.beta_home : 0);
      for (const basis of ["xg", "goals"] as const) {
        const gA = sm.gamma[`${basis}_A`];
        const gD = sm.gamma[`${basis}_D`];
        if (gA !== undefined) eta += gA * Math.log(own[basis].A[ki] / muT[basis]);
        if (gD !== undefined) eta += gD * Math.log(opp[basis].D[ki] / muT[basis]);
      }
      const nonOg = Math.max(Math.exp(eta) - mu.owngoal, 0.05);
      const old = sumCh(lamModel[t]);
      for (const c of SHOT_CHANNELS) lamModel[t][c] = (nonOg * lamModel[t][c]) / old;
    }
  }

  // §3 市场定锚
  let market: MatchConfig["market"] = null;
  let lamMkt: [number, number] | null = null;
  if (setup.fixtureId != null) {
    const fx = params.fixtures[String(setup.fixtureId)];
    if (fx && fx.market_lambda && fx.home_team_id === setup.home.teamId && fx.away_team_id === setup.away.teamId) {
      lamMkt = [fx.market_lambda.home, fx.market_lambda.away];
      market = {
        fixtureId: fx.match_id,
        status: fx.status,
        ahLine: fx.ah?.line ?? null,
        ouLine: fx.ou?.line ?? null,
        finalScore: fx.final_score,
      };
    }
  }
  // v0.2 第 3 条:市场 λ 含乌龙,先扣掉乌龙再混合;乌龙渠道另外叠加。
  const lam = [0, 1].map((t) => {
    const model = sumCh(lamModel[t]);
    const base = lamMkt ? marketW * (lamMkt[t] - mu.owngoal) + (1 - marketW) * model : model;
    const out = {} as Record<ShotChannel, number>;
    for (const c of SHOT_CHANNELS) out[c] = (base * lamModel[t][c]) / model;
    return out;
  });
  const lamBase = lam.map(sumCh);

  // §4 阵容调整
  const lineupAdj = [0, 1].map((t) => {
    const s = sides[t];
    const ll = teamP[t].last_lineup;
    // v0.2 第 2 条:R 也按它在参照阵型中的实际位置计算 f_pos,U = R 时比值严格为 1。
    const ref = (ll?.starters ?? []).map((r) => {
      const p = params.players[r.player_id];
      const g = slotGroup(params, ll!.formation, r.position_id) as PosGroup;
      return { p, g, f: p ? fPos(p.main_position, g) : 1 };
    });
    const sumR = ref.reduce((acc, r) => acc + (r.p?.a_p ?? 0) * r.f, 0);
    let sumU = 0;
    const defU: number[] = [];
    let gkG: number | null = null;
    for (const slot of s.slots) {
      const p = params.players[slot.playerId as string];
      const f = fPos(p.main_position, slot.group);
      sumU += p.a_p * f;
      if (DEFENSIVE_GROUPS.has(slot.group)) defU.push(p.r_p * f);
      if (slot.group === "GK") gkG = p.g_p ?? 0;
    }
    const defR = ref.filter((r) => DEFENSIVE_GROUPS.has(r.g)).map((r) => (r.p?.r_p ?? 0) * r.f);
    const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);
    const rAtt = sumR > 0 ? clamp(sumU / sumR, R_ATT_CLAMP) : 1;
    // 没有参照阵容(该队此前没有联赛首发)时不做防守比调整
    const dr = defR.length && defU.length ? mean(defU) - mean(defR) : 0;
    const mDef = clamp(1 - M_DEF_SLOPE * (dr / M_DEF_UNIT), M_DEF_CLAMP);
    const mGk = clamp(1 - (M_GK_COEF * (gkG ?? 0)) / league.gk_xgot_faced_per90, M_GK_CLAMP);
    return { rAtt, mDef, mGk };
  });
  for (let t = 0; t < 2; t++) {
    const o = 1 - t;
    for (const c of SHOT_CHANNELS) {
      if (c !== "penalty") lam[t][c] *= lineupAdj[t].rAtt;
      lam[t][c] *= lineupAdj[o].mDef;
    }
  }

  // §5 侧重点
  const shotCountMult = [0, 1].map(() => ({ open: 1, counter: 1, setpiece: 1, penalty: 1 }) as Record<ShotChannel, number>);
  const xgMult = [0, 1].map(() => ({ open: 1, counter: 1, setpiece: 1, penalty: 1 }) as Record<ShotChannel, number>);
  const own = [0, 1].map(() => ({ open: 1, counter: 1, setpiece: 1, penalty: 1 }) as Record<ShotChannel, number>);
  const opp = [0, 1].map(() => ({ open: 1, counter: 1, setpiece: 1, penalty: 1 }) as Record<ShotChannel, number>);
  for (let t = 0; t < 2; t++) {
    const o = 1 - t;
    for (const f of sides[t].focuses) {
      if (f === "setpiece") {
        const s = clamp((teamP[t].A.setpiece / mu.setpiece) * (teamP[o].D.setpiece / mu.setpiece), [0.5, 1.5]);
        own[t].setpiece *= 1 + 0.2 * s;
        own[t].open *= 0.95;
      } else if (f === "counter") {
        const sc = clamp(teamP[t].A.counter / mu.counter, [0.5, 1.5]);
        own[t].counter *= 1 + 0.3 * sc;
        own[t].open *= 0.92;
        opp[o].open *= 1.03;
      } else if (f === "possession") {
        own[t].open *= 1.1;
        own[t].counter *= 0.85;
        opp[o].counter *= 1.1;
      } else if (f === "press") {
        own[t].open *= 1.06;
        own[t].counter *= 1.1;
        opp[o].counter *= 1.15;
      } else if (f === "crossing") {
        shotCountMult[t].open *= 1.1;
        xgMult[t].open *= 0.9;
        own[t].open *= 1.1 * 0.9;
        own[t].setpiece *= 1.08;
      } else if (f === "lowblock") {
        own[t].open *= 0.85;
        own[t].counter *= 1.15;
        opp[o].open *= 0.85;
        opp[o].setpiece *= 1.05;
      }
    }
  }
  const applyCapped = (base: Record<ShotChannel, number>, mult: Record<ShotChannel, number>, cap: [number, number]) => {
    const before = sumCh(base);
    const after = SHOT_CHANNELS.reduce((s, c) => s + base[c] * mult[c], 0);
    const ratio = before > 0 ? after / before : 1;
    const k = ratio > 0 ? clamp(ratio, cap) / ratio : 1;
    for (const c of SHOT_CHANNELS) base[c] *= mult[c] * k;
    return ratio * k;
  };
  const focusOwnRatio = [0, 1].map((t) => applyCapped(lam[t], own[t], FOCUS_OWN_CAP));
  const focusOppRatio = [0, 1].map((t) => applyCapped(lam[t], opp[t], FOCUS_OPP_CAP));

  // 休息天数(假设设定)
  for (let t = 0; t < 2; t++) {
    if (!sides[t].shortRest) continue;
    for (const c of SHOT_CHANNELS) {
      lam[t][c] *= REST_OWN;
      lam[1 - t][c] *= REST_OPP;
    }
  }

  const teams = [0, 1].map((t): TeamConfig => {
    const s = sides[t];
    const lambda = { ...lam[t], owngoal: mu.owngoal } as Record<Channel, number>;
    const players = s.slots.map((slot): SimPlayer => {
      const p = params.players[slot.playerId as string];
      return {
        id: p.player_id,
        name: playerName(p, p.player_id),
        slotGroup: slot.group,
        fPos: fPos(p.main_position, slot.group),
        shots90: p.shots90 ?? SHOTS90_FALLBACK,
        headers90: p.headers90 ?? 0,
        npxg90: p.npxg90 ?? 0,
        pensTaken: p.penalties_taken,
      };
    });
    return {
      teamId: s.teamId,
      name: teamP[t].name_zh ?? String(s.teamId),
      lambda,
      shotCountMult: shotCountMult[t],
      xgMult: xgMult[t],
      pressLate: s.focuses.includes("press"),
      headerBoost: s.focuses.includes("crossing") ? HEADER_BOOST_CROSSING : 1,
      mGk: lineupAdj[t].mGk,
      players,
      breakdown: {
        lambdaModel: sumCh(lamModel[t]),
        lambdaMarket: lamMkt ? lamMkt[t] : null,
        lambdaBase: lamBase[t],
        rAtt: lineupAdj[t].rAtt,
        mDef: lineupAdj[t].mDef,
        mGk: lineupAdj[t].mGk,
        focusOwnRatio: focusOwnRatio[t],
        focusOppRatio: focusOppRatio[t],
        lambdaFinal: sumCh(lam[t]) + mu.owngoal,
        expectedGoals: sumCh(lam[t]) * lineupAdj[1 - t].mGk + mu.owngoal,
        lambdaByChannel: lambda,
      },
    };
  });

  const xgQuantiles = {} as Record<ShotChannel, number[]>;
  const xgMean = {} as Record<ShotChannel, number>;
  const xgScale = {} as Record<ShotChannel, number>;
  for (const c of SHOT_CHANNELS) {
    xgQuantiles[c] = league.shot_xg_quantiles[c];
    xgMean[c] = league.shot_xg_mean[c];
    xgScale[c] = league.shot_xg_mean[c] / interpMean(league.shot_xg_quantiles[c]);
  }
  // §7.3:点球每脚 xG 取联赛点球转化率;射门次数也按它换算,点球渠道的期望进球才等于 λ。
  xgMean.penalty = league.penalty_conversion;
  const dist = (d: Record<string, number> | undefined, fallback: number): [number[], number[]] => {
    const entries = Object.entries(d ?? {}).map(([k, v]) => [Number(k), v] as const).sort((a, b) => a[0] - b[0]);
    if (!entries.length) return [[fallback], [1]];
    const total = entries.reduce((s2, [, v]) => s2 + v, 0);
    let acc = 0;
    return [entries.map(([k]) => k), entries.map(([, v]) => (acc += v / total))];
  };

  return {
    ok: true,
    config: {
      kappa: setup.chaos ? KAPPA_CHAOS : (cal.kappa ?? KAPPA_STANDARD),
      redOwn: cal.red_own ?? RED_OWN,
      redOpp: cal.red_opp ?? RED_OPP,
      state: { ...V02_STATE, ...(cal.state ?? {}) },
      rateModel: cal.rate_model ?? null,
      effects: setup.effects ?? ALL_EFFECTS,
      timing: league.goal_timing.factor,
      stoppageDist: [
        dist(league.stoppage_distribution?.first_half, Math.round(league.stoppage_mean.first_half)),
        dist(league.stoppage_distribution?.second_half, Math.round(league.stoppage_mean.second_half)),
      ],
      xgQuantiles,
      xgMean,
      xgScale,
      penaltyConversion: league.penalty_conversion,
      redCardRate: league.red_card_rate,
      teams: [teams[0], teams[1]],
      market,
    },
  };
}

// ------------------------------------------------------------------ 逐分钟模拟(§6–§7,v0.2)
export type EventKind = "shot" | "goal" | "red" | "injury" | "gk_error";

export interface SimEvent {
  tick: number;
  /** 常规时间分钟(补时记在 45 / 90) */
  minute: number;
  /** 补时分钟数(常规时间为 0),供 xG 赛跑图展开补时横轴 */
  added: number;
  clock: string;
  team: 0 | 1;
  kind: EventKind;
  channel?: Channel | "gk_error";
  playerId?: string;
  playerName?: string;
  xg?: number;
  isGoal?: boolean;
  score?: [number, number];
}

export interface SingleResult {
  seed: number;
  score: [number, number];
  events: SimEvent[];
  totalTicks: number;
  halfTimeTick: number;
  stoppage: [number, number];
  epsilon: [number, number];
}

interface MinuteTick {
  tick: number;
  half: 1 | 2;
  minute: number;
  added: number;
  clock: string;
  bucket: number;
}

function buildTicks(stoppage: [number, number]): MinuteTick[] {
  const ticks: MinuteTick[] = [];
  let tick = 0;
  for (let m = 1; m <= 45 + stoppage[0]; m++) {
    const minute = Math.min(m, 45);
    ticks.push({ tick: tick++, half: 1, minute, added: Math.max(0, m - 45), clock: m > 45 ? `45+${m - 45}'` : `${m}'`, bucket: Math.min(Math.floor((minute - 1) / 15), 2) });
  }
  for (let m = 46; m <= 90 + stoppage[1]; m++) {
    const minute = Math.min(m, 90);
    ticks.push({ tick: tick++, half: 2, minute, added: Math.max(0, m - 90), clock: m > 90 ? `90+${m - 90}'` : `${m}'`, bucket: 3 + Math.min(Math.floor((minute - 46) / 15), 2) });
  }
  return ticks;
}

function sampleStoppage(rng: Rng, [minutes, cum]: [number[], number[]]): number {
  const u = rng();
  const i = cum.findIndex((c) => u <= c);
  return minutes[i === -1 ? minutes.length - 1 : i];
}

function sampleXg(cfg: MatchConfig, c: ShotChannel, rng: Rng): number {
  const q = cfg.xgQuantiles[c];
  const pos = rng() * (q.length - 1);
  const i = Math.min(Math.floor(pos), q.length - 2);
  const v = q[i] + (q[i + 1] - q[i]) * (pos - i);
  return Math.min(0.95, v * cfg.xgScale[c]);
}

// §7.2:以 T 视角的净胜球 d,给出 T 在渠道 c 上的乘数(落后 1 球时 open、counter 各有乘数)。
function stateMult(d: number, c: Channel, st: StateMultipliers): number {
  if (d === -1) return c === "open" ? st.trail1_open : c === "counter" ? st.trail1_counter : 1;
  if (d === 1) return st.lead1_all;
  if (d <= -2) return st.trail2_all;
  if (d >= 2) return st.lead2_all;
  return 1;
}

// v0.2 第 4 条:乌龙渠道只吃"作用于本队所有渠道"的比分状态乘数。
function stateMultAll(d: number, st: StateMultipliers): number {
  if (d === 1) return st.lead1_all;
  if (d <= -2) return st.trail2_all;
  if (d >= 2) return st.lead2_all;
  return 1;
}

interface Scheduled {
  red: { half: 1 | 2; minute: number } | null;
  injury: { half: 1 | 2; minute: number } | null;
  gkError: { half: 1 | 2; minute: number } | null;
}

function scheduleMinute(rng: Rng, lo: number, hi: number): { half: 1 | 2; minute: number } {
  const minute = lo + Math.floor(rng() * (hi - lo + 1));
  return { half: minute <= 45 ? 1 : 2, minute };
}

interface MatchRun {
  score: [number, number];
  events: SimEvent[];
  scorers: string[];
  ticks: MinuteTick[];
  stoppage: [number, number];
  epsilon: [number, number];
  /** 红牌张数、点球射门次数、按 6 段计时的进球数(回测闸门用) */
  reds: number;
  penalties: number;
  goalBuckets: number[];
}

function runMatch(cfg: MatchConfig, rng: Rng, record: boolean): MatchRun {
  const fx = cfg.effects;
  const stoppage: [number, number] = [sampleStoppage(rng, cfg.stoppageDist[0]), sampleStoppage(rng, cfg.stoppageDist[1])];
  const ticks = buildTicks(stoppage);
  // v0.2 第 1 条:按本场实际模拟的全部分钟归一化,关闭所有事件时期望进球恰好等于 λ。
  const timingSum = ticks.reduce((acc, tk) => acc + cfg.timing[tk.bucket], 0);
  const eps: [number, number] = fx.epsilon && cfg.kappa > 0
    ? [gamma(rng, cfg.kappa, 1 / cfg.kappa), gamma(rng, cfg.kappa, 1 / cfg.kappa)]
    : [1, 1];
  const rm = cfg.rateModel;
  // v0.3:η = 时段(或末段格子)+ 75 分钟前的比分状态 + 红牌,均为回归系数
  const eta = (tk: MinuteTick, d: number, ownDown: boolean, oppDown: boolean): number => {
    const r = rm as RateModel;
    let e: number;
    if (tk.bucket === 5) {
      e = d === 0 ? r.late.level : d === 1 ? r.late.lead1 : d === -1 ? r.late.trail1 : d >= 2 ? r.late.lead2 : r.late.trail2;
    } else {
      e = r.periods[tk.bucket] + (d === 0 ? 0 : d === 1 ? r.early_state.lead1 : d === -1 ? r.early_state.trail1 : d >= 2 ? r.early_state.lead2 : r.early_state.trail2);
    }
    return e + (ownDown ? r.red_own_down : 0) + (oppDown ? r.red_opp_down : 0);
  };
  const sched: Scheduled[] = [0, 1].map(() => ({
    red: fx.redCard && rng() < cfg.redCardRate ? scheduleMinute(rng, 20, 90) : null,
    injury: fx.injury && rng() < INJURY_P ? scheduleMinute(rng, 1, 90) : null,
    gkError: fx.gkError && rng() < GK_ERROR_P ? scheduleMinute(rng, 1, 90) : null,
  }));
  const score: [number, number] = [0, 0];
  const events: SimEvent[] = [];
  const scorers: string[] = [];
  const goalBuckets = [0, 0, 0, 0, 0, 0];
  let reds = 0;
  let penalties = 0;
  const onPitch = cfg.teams.map((t) => [...t.players]);
  const red = [false, false];
  const injured = [false, false];
  const concededTicks: number[][] = [[], []];
  const collapseUntil = [-1, -1];

  const pickShooter = (t: number, c: ShotChannel | "gk_error"): SimPlayer | null => {
    const ps = onPitch[t];
    if (c === "penalty") {
      const best = Math.max(...ps.map((p) => p.pensTaken));
      const pool = best > 0 ? ps.filter((p) => p.pensTaken === best) : ps.filter((p) => p.npxg90 === Math.max(...ps.map((x) => x.npxg90)));
      return pool[0] ?? null;
    }
    const w = ps.map((p) => p.shots90 * p.fPos);
    if (c === "setpiece") {
      const teamH = ps.reduce((s2, p) => s2 + p.headers90, 0);
      const boost = cfg.teams[t].headerBoost;
      for (let i = 0; i < ps.length; i++) w[i] *= 1 + (teamH > 0 ? (boost * ps[i].headers90) / teamH : 0);
    }
    return weightedPick(rng, ps, w);
  };

  const addGoal = (t: 0 | 1, tk: MinuteTick) => {
    score[t] += 1;
    goalBuckets[tk.bucket] += 1;
    const o = 1 - t;
    const prev = concededTicks[o][concededTicks[o].length - 1];
    concededTicks[o].push(tk.tick);
    if (fx.collapse && prev !== undefined && tk.tick - prev <= COLLAPSE_WINDOW) collapseUntil[t] = tk.tick + COLLAPSE_WINDOW;
  };

  for (const tk of ticks) {
    const base = { tick: tk.tick, minute: tk.minute, added: tk.added, clock: tk.clock };
    for (let t = 0; t < 2; t++) {
      const s = sched[t];
      if (tk.clock.includes("+")) continue;
      if (s.red && !red[t] && s.red.half === tk.half && s.red.minute === tk.minute) {
        red[t] = true;
        reds += 1;
        const cands = onPitch[t].filter((p) => p.slotGroup !== "GK");
        const off = cands[Math.floor(rng() * cands.length)];
        if (off) onPitch[t] = onPitch[t].filter((p) => p.id !== off.id);
        if (record) events.push({ ...base, team: t as 0 | 1, kind: "red", playerId: off?.id, playerName: off?.name });
      }
      if (s.injury && !injured[t] && s.injury.half === tk.half && s.injury.minute === tk.minute) {
        injured[t] = true;
        if (record) events.push({ ...base, team: t as 0 | 1, kind: "injury" });
      }
    }

    const order: (0 | 1)[] = rng() < 0.5 ? [0, 1] : [1, 0];
    for (const t of order) {
      const o = (1 - t) as 0 | 1;
      const team = cfg.teams[t];
      const d = score[t] - score[o];
      const press = team.pressLate && tk.half === 2 && tk.minute > PRESS_LATE_FROM ? PRESS_LATE_MULT : 1;
      const otherEvents = (injured[t] ? INJURY_MULT : 1) * (tk.tick <= collapseUntil[t] ? COLLAPSE_MULT : 1);
      // v0.2:timing 归一化 × 分渠道比分状态 × 红牌乘数;v0.3:exp(η)/90(对所有渠道相同)
      const minuteMult = rm ? Math.exp(eta(tk, fx.scoreState ? d : 0, red[t], red[o])) / 90 : cfg.timing[tk.bucket] / timingSum;
      const eventMult = rm ? 1 : (red[t] ? cfg.redOwn : 1) * (red[o] ? cfg.redOpp : 1);
      const shotEventMult = eventMult * otherEvents;

      for (const c of SHOT_CHANNELS) {
        const state = rm || !fx.scoreState ? 1 : stateMult(d, c, cfg.state);
        const rate = team.lambda[c] * minuteMult * eps[t] * state * shotEventMult * press;
        const perShot = cfg.xgMean[c] * team.xgMult[c];
        const n = poisson(rng, rate / perShot);
        if (c === "penalty") penalties += n;
        for (let k = 0; k < n; k++) {
          const xg = c === "penalty" ? cfg.penaltyConversion : sampleXg(cfg, c, rng) * team.xgMult[c];
          const isGoal = rng() < Math.min(1, xg * cfg.teams[o].mGk);
          const shooter = pickShooter(t, c);
          if (isGoal) {
            addGoal(t, tk);
            if (shooter) scorers.push(`${t}:${shooter.id}`);
          }
          if (record) {
            events.push({ ...base, team: t, kind: isGoal ? "goal" : "shot", channel: c, playerId: shooter?.id, playerName: shooter?.name, xg, isGoal, score: [score[0], score[1]] });
          }
        }
      }

      const ogState = rm || !fx.scoreState ? 1 : stateMultAll(d, cfg.state);
      const ogRate = team.lambda.owngoal * minuteMult * ogState * eventMult;
      const nOg = poisson(rng, ogRate);
      for (let k = 0; k < nOg; k++) {
        addGoal(t, tk);
        const cands = onPitch[o].filter((p) => p.slotGroup !== "GK");
        const who = cands[Math.floor(rng() * cands.length)];
        if (record) events.push({ ...base, team: t, kind: "goal", channel: "owngoal", playerId: who?.id, playerName: who?.name, isGoal: true, score: [score[0], score[1]] });
      }

      const gkErr = sched[o].gkError;
      if (gkErr && gkErr.half === tk.half && gkErr.minute === tk.minute && !tk.clock.includes("+")) {
        const shooter = pickShooter(t, "gk_error");
        const isGoal = rng() < GK_ERROR_XG;
        if (isGoal) {
          addGoal(t, tk);
          if (shooter) scorers.push(`${t}:${shooter.id}`);
        }
        if (record) events.push({ ...base, team: t, kind: isGoal ? "goal" : "shot", channel: "gk_error", playerId: shooter?.id, playerName: shooter?.name, xg: GK_ERROR_XG, isGoal, score: [score[0], score[1]] });
      }
    }
  }
  return { score, events, scorers, ticks, stoppage, epsilon: eps, reds, penalties, goalBuckets };
}

export function simulateOnce(cfg: MatchConfig, seed: number): SingleResult {
  const r = runMatch(cfg, mulberry32(seed), true);
  const halfTimeTick = r.ticks.findIndex((t) => t.half === 2);
  return { seed, score: r.score, events: r.events, totalTicks: r.ticks.length, halfTimeTick, stoppage: r.stoppage, epsilon: r.epsilon };
}

/** 本次比分在 N 次模拟中的出现率分级:≥10% 常见,3%–10% 少见,<3% 罕见。 */
export function rarityTag(scoreCount: number, runs: number): "常见" | "少见" | "罕见" {
  const r = scoreCount / runs;
  if (r >= 0.1) return "常见";
  if (r >= 0.03) return "少见";
  return "罕见";
}

export interface ManyResult {
  runs: number;
  pHome: number;
  pDraw: number;
  pAway: number;
  topScores: { score: string; p: number }[];
  meanGoals: [number, number];
  fairAhLine: number;
  fairOuLine: number;
  crown: { ahLine: number | null; pEffAhHome: number | null; ouLine: number | null; pEffOver: number | null } | null;
  upset: { scoreCount: number; outcomeShare: number; outcome: "H" | "D" | "A" };
  scorerProb: { team: 0 | 1; playerId: string; name: string; p: number }[];
  /** 净胜 ≥3 球(任一方)的比例 */
  pBigMargin: number;
  meanReds: number;
  meanPenalties: number;
  /** 6 段计时的场均进球数 */
  goalBuckets: number[];
}

export function simulateMany(cfg: MatchConfig, seed: number, runs: number, singleScore: [number, number]): ManyResult {
  const rng = mulberry32((seed ^ 0x9e3779b9) >>> 0);
  let h = 0;
  let d = 0;
  let a = 0;
  let gh = 0;
  let ga = 0;
  const scores = new Map<string, number>();
  const margin: IntDist = new Map();
  const total: IntDist = new Map();
  const scorerCount = new Map<string, number>();
  let big = 0;
  let redsSum = 0;
  let pensSum = 0;
  const buckets = [0, 0, 0, 0, 0, 0];
  for (let i = 0; i < runs; i++) {
    const r = runMatch(cfg, rng, false);
    const [x, y] = r.score;
    if (Math.abs(x - y) >= 3) big++;
    redsSum += r.reds;
    pensSum += r.penalties;
    for (let b = 0; b < 6; b++) buckets[b] += r.goalBuckets[b];
    gh += x;
    ga += y;
    if (x > y) h++;
    else if (x === y) d++;
    else a++;
    const key = `${x}-${y}`;
    scores.set(key, (scores.get(key) ?? 0) + 1);
    margin.set(x - y, (margin.get(x - y) ?? 0) + 1 / runs);
    total.set(x + y, (total.get(x + y) ?? 0) + 1 / runs);
    for (const s of new Set(r.scorers)) scorerCount.set(s, (scorerCount.get(s) ?? 0) + 1);
  }
  const names = new Map<string, string>();
  cfg.teams.forEach((t, i) => t.players.forEach((p) => names.set(`${i}:${p.id}`, p.name)));
  const [sx, sy] = singleScore;
  const outcome = sx > sy ? "H" : sx === sy ? "D" : "A";
  return {
    runs,
    pHome: h / runs,
    pDraw: d / runs,
    pAway: a / runs,
    topScores: [...scores.entries()].sort((p, q) => q[1] - p[1]).slice(0, 5).map(([score, n]) => ({ score, p: n / runs })),
    meanGoals: [gh / runs, ga / runs],
    fairAhLine: fairLine(margin, -4, 4),
    fairOuLine: fairLine(total, 0.5, 7),
    crown: cfg.market
      ? {
          ahLine: cfg.market.ahLine,
          pEffAhHome: cfg.market.ahLine != null ? pEff(margin, cfg.market.ahLine) : null,
          ouLine: cfg.market.ouLine,
          pEffOver: cfg.market.ouLine != null ? pEff(total, cfg.market.ouLine) : null,
        }
      : null,
    upset: {
      scoreCount: scores.get(`${sx}-${sy}`) ?? 0,
      outcomeShare: outcome === "H" ? h / runs : outcome === "D" ? d / runs : a / runs,
      outcome,
    },
    pBigMargin: big / runs,
    meanReds: redsSum / runs,
    meanPenalties: pensSum / runs,
    goalBuckets: buckets.map((b) => b / runs),
    scorerProb: [...scorerCount.entries()]
      .sort((p, q) => q[1] - p[1])
      .slice(0, 10)
      .map(([k, n]) => ({ team: Number(k.split(":")[0]) as 0 | 1, playerId: k.slice(2), name: names.get(k) ?? k.slice(2), p: n / runs })),
  };
}
