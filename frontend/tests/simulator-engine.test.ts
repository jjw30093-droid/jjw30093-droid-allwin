import { describe, expect, it } from "vitest";
import {
  focusError,
  fPos,
  prepareMatch,
  simulateMany,
  simulateOnce,
  type MatchSetup,
  type SlotAssign,
} from "@/features/simulator/engine";
import { marginDist, pEff, poissonPmf, totalDist } from "@/features/simulator/market";
import type { PlayerParams, PosGroup, SimParams, TeamParams } from "@/features/simulator/types";

// research/ah_signals/features_market.py::p_eff 在同样输入上的输出(2026-09-29 生成)。
const GOLDEN: [string, number, number, number, number][] = [
  ["margin", 1.4, 1.1, -1.25, 0.87309778198], ["margin", 1.4, 1.1, -0.75, 0.77411190796],
  ["margin", 1.4, 1.1, -0.5, 0.704387496018], ["margin", 1.4, 1.1, -0.25, 0.658971948665],
  ["margin", 1.4, 1.1, 0.0, 0.597068815139], ["margin", 1.4, 1.1, 0.25, 0.505339924509],
  ["margin", 1.4, 1.1, 0.5, 0.438042559436], ["margin", 1.4, 1.1, 0.75, 0.365281763923],
  ["margin", 1.4, 1.1, 1.0, 0.270876775151], ["margin", 1.4, 1.1, 1.5, 0.208772967427],
  ["total", 1.4, 1.1, 1.5, 0.712702416035], ["total", 1.4, 1.1, 2.25, 0.523304644679],
  ["total", 1.4, 1.1, 2.5, 0.456186716066], ["total", 1.4, 1.1, 2.75, 0.39110732865],
  ["total", 1.4, 1.1, 3.0, 0.308334075406], ["total", 1.4, 1.1, 3.25, 0.271435026891],
  ["margin", 2.05, 0.72, -1.25, 0.965998553528], ["margin", 2.05, 0.72, -0.75, 0.920142967864],
  ["margin", 2.05, 0.72, -0.5, 0.879799257638], ["margin", 2.05, 0.72, -0.25, 0.866782080526],
  ["margin", 2.05, 0.72, 0.0, 0.850603095921], ["margin", 2.05, 0.72, 0.25, 0.758486766743],
  ["margin", 2.05, 0.72, 0.5, 0.684372438744], ["margin", 2.05, 0.72, 0.75, 0.639350141],
  ["margin", 2.05, 0.72, 1.0, 0.579346558208], ["margin", 2.05, 0.72, 1.5, 0.43469926339],
  ["total", 2.05, 0.72, 1.5, 0.76376178049], ["total", 2.05, 0.72, 2.25, 0.594862773107],
  ["total", 2.05, 0.72, 2.5, 0.523359627376], ["total", 2.05, 0.72, 2.75, 0.463855247832],
  ["total", 2.05, 0.72, 3.0, 0.387374288739], ["total", 2.05, 0.72, 3.25, 0.33901399862],
];

const SLOTS: [number, PosGroup][] = [
  [11, "GK"], [32, "FB"], [34, "CB"], [36, "CB"], [38, "FB"],
  [72, "W"], [74, "CM"], [76, "CM"], [78, "W"], [104, "ST"], [106, "ST"],
];

function player(id: string, pos: PosGroup, over: Partial<PlayerParams> = {}): PlayerParams {
  return {
    player_id: id, name_zh: id, name_en: id, shirt_number: "1", main_position: pos,
    position_source: "rule_decoded", position_unverified: true, minutes: 900,
    a_p: pos === "ST" ? 0.5 : pos === "W" ? 0.3 : 0.1, npxg90: 0.2, xa90: 0.1,
    shots90: pos === "ST" ? 3 : 1, headers90: pos === "CB" ? 0.5 : 0.1,
    penalties_taken: 0, penalties_scored: 0, r_p: 6.8, g_p: pos === "GK" ? 0 : null, ...over,
  };
}

function team(id: number, A: number[], D: number[], prefix: string): TeamParams {
  const ch = (v: number[]) => ({ open: v[0], counter: v[1], setpiece: v[2], penalty: v[3] });
  return {
    team_id: id, league_id: 1, name_zh: prefix, name_en: prefix, A: ch(A), D: ch(D),
    window_matches: 10, n_eff: 8.67,
    last_lineup: {
      match_id: 1, date: "2026-09-19", formation: "4-4-2", formation_has_template: true,
      starters: SLOTS.map(([pid, g], i) => ({ player_id: `${prefix}${i}`, position_id: pid, slot_group: g })),
    },
    squad: [...SLOTS.map((_, i) => `${prefix}${i}`), `${prefix}gk2`, `${prefix}cb2`],
  };
}

function buildParams(): SimParams {
  const players: Record<string, PlayerParams> = {};
  for (const prefix of ["h", "a"]) {
    SLOTS.forEach(([, g], i) => (players[`${prefix}${i}`] = player(`${prefix}${i}`, g)));
    players[`${prefix}gk2`] = player(`${prefix}gk2`, "GK");
    players[`${prefix}cb2`] = player(`${prefix}cb2`, "CB", { a_p: 0.02, r_p: 6.2 });
  }
  players.h9 = player("h9", "ST", { penalties_taken: 5, a_p: 0.6 });
  const q = (lo: number, hi: number) => Array.from({ length: 19 }, (_, i) => lo + ((hi - lo) * i) / 18);
  return {
    meta: { generated_at: "2026-09-29T00:00:00Z", model_version: "v0.1", uncalibrated: true },
    leagues: {
      "1": {
        league_id: 1, finished_matches: 400,
        mu: { open: 0.8, counter: 0.15, setpiece: 0.35, penalty: 0.1, owngoal: 0.05 },
        shot_xg_quantiles: { probs: [], open: q(0.02, 0.4), counter: q(0.03, 0.5), setpiece: q(0.02, 0.35), penalty: q(0.79, 0.79) },
        shot_xg_mean: { open: 0.1, counter: 0.16, setpiece: 0.1, penalty: 0.79 },
        red_card_rate: 0.06, penalty_rate: 0.12, penalty_conversion: 0.8,
        goal_timing: { buckets: [], goals: [], factor: [0.7, 0.7, 1.2, 0.95, 0.9, 1.5] },
        stoppage_mean: { first_half: 3, second_half: 6 }, gk_xgot_faced_per90: 1.35,
      },
    },
    formations: {},
    teams: { "10": team(10, [1.0, 0.2, 0.4, 0.1], [0.7, 0.1, 0.3, 0.1], "h"), "20": team(20, [0.7, 0.15, 0.3, 0.1], [0.9, 0.2, 0.4, 0.1], "a") },
    players,
    fixtures: {
      "99": {
        match_id: 99, league_id: 1, kickoff_at_utc: "2026-10-01T19:00:00Z", home_team_id: 10, away_team_id: 20,
        status: "未开赛", final_score: null,
        ah: { line: 0.5, home: 0.95, away: 0.93, observed_at: "2026-09-29T00:00:00Z" },
        ou: { line: 2.5, over: 0.9, under: 0.98, observed_at: "2026-09-29T00:00:00Z" },
        market_lambda: { home: 1.6, away: 1.0 },
      },
    },
  };
}

function slotsFor(prefix: string, override: Record<number, string> = {}): SlotAssign[] {
  return SLOTS.map(([pid, g], i) => ({ positionId: pid, group: g, playerId: override[i] ?? `${prefix}${i}`, x: 0.5, y: 0.5 }));
}

function setup(over: Partial<MatchSetup> = {}): MatchSetup {
  return {
    leagueId: 1,
    home: { teamId: 10, formation: "4-4-2", slots: slotsFor("h"), focuses: [], shortRest: false },
    away: { teamId: 20, formation: "4-4-2", slots: slotsFor("a"), focuses: [], shortRest: false },
    fixtureId: null,
    chaos: false,
    ...over,
  };
}

describe("p_eff 与 Python 研究实现一致", () => {
  it.each(GOLDEN)("%s λ=(%s,%s) line=%s", (kind, lh, la, line, want) => {
    const ph = poissonPmf(lh);
    const pa = poissonPmf(la);
    const dist = kind === "margin" ? marginDist(ph, pa) : totalDist(ph, pa);
    expect(pEff(dist, line)).toBeCloseTo(want, 9);
  });
});

describe("位置兼容 §4.3", () => {
  it("本位置 / 相邻 / 其他 / 门将互换", () => {
    expect(fPos("CB", "CB")).toBe(1);
    expect(fPos("CB", "FB")).toBe(0.9);
    expect(fPos("W", "AM")).toBe(0.9);
    expect(fPos("CB", "ST")).toBe(0.7);
    expect(fPos("ST", "GK")).toBe(0.3);
    expect(fPos("GK", "CB")).toBe(0.3);
  });
});

describe("侧重点互斥 §5", () => {
  it("互斥组合与数量上限", () => {
    expect(focusError(["possession", "counter"])).toContain("不能同时选");
    expect(focusError(["press", "lowblock"])).toContain("不能同时选");
    expect(focusError(["setpiece", "crossing", "press"])).toContain("最多");
    expect(focusError(["setpiece", "press"])).toBeNull();
  });
});

describe("λ 组装", () => {
  it("数据模型 λ = μ·(A/μ)·(D_opp/μ)·HA", () => {
    const r = prepareMatch(buildParams(), setup());
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    const open = 0.8 * (1.0 / 0.8) * (0.9 / 0.8) * 1.08;
    expect(r.config.teams[0].lambda.open).toBeCloseTo(open, 9);
    expect(r.config.teams[0].lambda.owngoal).toBe(0.05);
    expect(r.config.teams[0].breakdown.rAtt).toBeCloseTo(1, 9);
    expect(r.config.teams[0].breakdown.mDef).toBeCloseTo(1, 9);
  });

  it("有 Crown λ 时按 w=0.7 混合,渠道按模型占比分配", () => {
    const r = prepareMatch(buildParams(), setup({ fixtureId: 99 }));
    if (!r.ok) throw new Error(r.error);
    const b = r.config.teams[0].breakdown;
    expect(b.lambdaBase).toBeCloseTo(0.7 * 1.6 + 0.3 * b.lambdaModel, 9);
    expect(r.config.market?.ahLine).toBe(0.5);
  });

  it("换下强力前锋 → 进攻比下降,且截断在 0.85", () => {
    const r = prepareMatch(buildParams(), setup({ home: { ...setup().home, slots: slotsFor("h", { 9: "hcb2", 10: "hgk2" }) } }));
    if (!r.ok) throw new Error(r.error);
    expect(r.config.teams[0].breakdown.rAtt).toBe(0.85);
  });

  it("首发没有门将 → 禁止模拟", () => {
    const r = prepareMatch(buildParams(), setup({ home: { ...setup().home, slots: slotsFor("h", { 0: "hcb2" }) } }));
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain("门将");
  });

  it("侧重点把本队总 λ 截断在 [0.85, 1.15]", () => {
    const r = prepareMatch(buildParams(), setup({ home: { ...setup().home, focuses: ["lowblock", "setpiece"] } }));
    if (!r.ok) throw new Error(r.error);
    const ratio = r.config.teams[0].breakdown.focusOwnRatio;
    expect(ratio).toBeGreaterThanOrEqual(0.85 - 1e-12);
    expect(ratio).toBeLessThanOrEqual(1.15 + 1e-12);
  });
});

describe("模拟", () => {
  it("同一种子复现同一结果", () => {
    const r = prepareMatch(buildParams(), setup());
    if (!r.ok) throw new Error(r.error);
    const a = simulateOnce(r.config, 12345);
    const b = simulateOnce(r.config, 12345);
    expect(b).toEqual(a);
    const many1 = simulateMany(r.config, 12345, 200, a.score);
    const many2 = simulateMany(r.config, 12345, 200, a.score);
    expect(many2).toEqual(many1);
  });

  it("1000 次汇总自洽", () => {
    const r = prepareMatch(buildParams(), setup({ fixtureId: 99 }));
    if (!r.ok) throw new Error(r.error);
    const single = simulateOnce(r.config, 7);
    const m = simulateMany(r.config, 7, 1000, single.score);
    expect(m.pHome + m.pDraw + m.pAway).toBeCloseTo(1, 9);
    expect(m.topScores).toHaveLength(5);
    expect(m.fairOuLine % 0.25).toBeCloseTo(0, 9);
    expect(m.crown?.pEffAhHome).not.toBeNull();
    const last = single.events.filter((e) => e.kind === "goal").at(-1);
    expect(last?.score ?? [0, 0]).toEqual(single.score);
  });
});
