// 侧重点的展示口径(只读引擎结果,不改乘数):渠道层面的预期进球变化 + 1000 次模拟的胜率净影响。
// 对照组一律是"本队不选侧重点、对手侧重点保持当前设定",其余设定(阵容、盘口、种子)完全相同。

import { FOCUS_LIST, prepareMatch, simulateMany, type Focus, type MatchConfig, type MatchSetup } from "./engine";
import type { ShotChannel, SimParams } from "./types";

export const CHANNEL_LABEL: Record<ShotChannel, string> = {
  open: "运动战",
  counter: "反击",
  setpiece: "定位球",
  penalty: "点球",
};
/** 同幅度时的展示顺序;默认按变化幅度从大到小 */
const CHANNEL_ORDER: ShotChannel[] = ["setpiece", "open", "counter", "penalty"];
/** 变化绝对值小于 0.5% 的渠道不展示 */
const MIN_SHOWN = 0.005;
/** 单一侧重点使本队胜率提升 ≥ 3 个百分点时标注"适合本场对手" */
export const FIT_THRESHOLD_PP = 3;
export const IMPACT_RUNS = 1000;

export interface ChannelDelta {
  channel: ShotChannel;
  /** 相对变化,0.18 = +18% */
  change: number;
}

function withFocuses(setup: MatchSetup, side: 0 | 1, focuses: Focus[]): MatchSetup {
  const key = side === 0 ? "home" : "away";
  return { ...setup, [key]: { ...setup[key], focuses } };
}

function deltas(base: Record<ShotChannel, number>, cur: Record<ShotChannel, number>): ChannelDelta[] {
  return CHANNEL_ORDER.filter((c) => base[c] > 0)
    .map((c) => ({ channel: c, change: cur[c] / base[c] - 1 }))
    .filter((d) => Math.abs(d.change) >= MIN_SHOWN)
    .sort((x, y) => Math.abs(y.change) - Math.abs(x.change));
}

/** 本队所选侧重点带来的渠道预期进球变化:本队各渠道、对手各渠道(相对本队不选侧重点)。 */
export function channelDeltas(
  params: SimParams,
  setup: MatchSetup,
  side: 0 | 1,
): { own: ChannelDelta[]; opp: ChannelDelta[] } | null {
  const cur = (side === 0 ? setup.home : setup.away).focuses;
  if (!cur.length) return null;
  const a = prepareMatch(params, withFocuses(setup, side, []));
  const b = prepareMatch(params, setup);
  if (!a.ok || !b.ok) return null;
  const o = side === 0 ? 1 : 0;
  return {
    own: deltas(a.config.teams[side].lambda, b.config.teams[side].lambda),
    opp: deltas(a.config.teams[o].lambda, b.config.teams[o].lambda),
  };
}

export function formatDeltas(ds: ChannelDelta[]): string {
  return ds
    .map((d) => {
      const pct = Math.round(d.change * 100);
      const sign = pct > 0 ? "+" : pct < 0 ? "−" : "±";
      return `${CHANNEL_LABEL[d.channel]} ${sign}${Math.abs(pct)}%`;
    })
    .join("，");
}

/** 胜率净影响要模拟的配置:每队一个对照(不选侧重点)、当前所选组合、6 个单一侧重点。 */
export interface ImpactJob {
  side: 0 | 1;
  kind: "base" | "selected" | Focus;
  config: MatchConfig;
}

export function impactJobs(params: SimParams, setup: MatchSetup): ImpactJob[] {
  const jobs: ImpactJob[] = [];
  for (const side of [0, 1] as const) {
    const base = prepareMatch(params, withFocuses(setup, side, []));
    if (!base.ok) continue;
    jobs.push({ side, kind: "base", config: base.config });
    if ((side === 0 ? setup.home : setup.away).focuses.length) {
      const sel = prepareMatch(params, setup);
      if (sel.ok) jobs.push({ side, kind: "selected", config: sel.config });
    }
    for (const f of FOCUS_LIST) {
      const r = prepareMatch(params, withFocuses(setup, side, [f]));
      if (r.ok) jobs.push({ side, kind: f, config: r.config });
    }
  }
  return jobs;
}

/** 本队胜率(主队取主胜,客队取客胜)。同一种子,减少对照之间的随机差异。 */
export function winRate(config: MatchConfig, side: 0 | 1, seed: number, runs: number): number {
  const m = simulateMany(config, seed, runs, [0, 0]);
  return side === 0 ? m.pHome : m.pAway;
}

export interface SideImpact {
  base: number;
  selected: number | null;
  /** 单一侧重点相对不选侧重点的胜率变化(百分点) */
  singlePp: Partial<Record<Focus, number>>;
}

export function summarizeImpact(results: { side: 0 | 1; kind: ImpactJob["kind"]; win: number }[]): [SideImpact | null, SideImpact | null] {
  return ([0, 1] as const).map((side) => {
    const rs = results.filter((r) => r.side === side);
    const base = rs.find((r) => r.kind === "base");
    if (!base) return null;
    const singlePp: SideImpact["singlePp"] = {};
    for (const r of rs) {
      if (r.kind !== "base" && r.kind !== "selected") singlePp[r.kind] = (r.win - base.win) * 100;
    }
    return { base: base.win, selected: rs.find((r) => r.kind === "selected")?.win ?? null, singlePp };
  }) as [SideImpact | null, SideImpact | null];
}
