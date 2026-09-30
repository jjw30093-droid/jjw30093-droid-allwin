import { describe, expect, it } from "vitest";
import { buildOption as buildXgRaceOption, cumulativeSeries } from "@/components/matches/XgRaceChart";
import type { ChartColors } from "@/components/charts/useChartColors";
import {
  focusError,
  fPos,
  NO_EFFECTS,
  prepareMatch,
  rarityTag,
  simulateMany,
  simulateOnce,
  slotGroup,
  type MatchSetup,
  type SlotAssign,
} from "@/features/simulator/engine";
import { cumulativeXg, toReportShots } from "@/features/simulator/xg";
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
    player_id: id, name_zh: id, name_en: id, shirt_number: "1", team_id: 0, usual_position_id: null,
    top_formation: "4-4-2", top_position_id: null, top_position_starts: 0, starts: 0, main_position: pos,
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
      match_id: 1, date: "2026-09-19", formation: "4-4-2", formation_has_template: true, formation_in_position_map: true,
      starters: SLOTS.map(([pid], i) => ({ player_id: `${prefix}${i}`, position_id: pid })),
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
        stoppage_mean: { first_half: 3, second_half: 6 },
        stoppage_distribution: { first_half: { "1": 10, "3": 60, "5": 30 }, second_half: { "4": 20, "6": 50, "9": 30 } },
        gk_xgot_faced_per90: 1.35,
      },
    },
    formations: {},
    position_map: { "4-4-2": Object.fromEntries(SLOTS.map(([pid, g]) => [String(pid), g])) },
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
  it("数据模型 λ = μ·(A/μ)·(D_opp/μ)·HA(未给主场系数时 HA = 1)", () => {
    const r = prepareMatch(buildParams(), setup());
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    const open = 0.8 * (1.0 / 0.8) * (0.9 / 0.8);
    expect(r.config.teams[0].lambda.open).toBeCloseTo(open, 9);
    expect(r.config.teams[0].lambda.owngoal).toBe(0.05);
    expect(r.config.teams[0].breakdown.rAtt).toBeCloseTo(1, 9);
    expect(r.config.teams[0].breakdown.mDef).toBeCloseTo(1, 9);
  });

  it("有 Crown λ 时先扣乌龙再按 w 混合,乌龙另外叠加", () => {
    const p = buildParams();
    p.calibration = { market_w: 0.7 };
    const r = prepareMatch(p, setup({ fixtureId: 99 }));
    if (!r.ok) throw new Error(r.error);
    const b = r.config.teams[0].breakdown;
    expect(b.lambdaBase).toBeCloseTo(0.7 * (1.6 - 0.05) + 0.3 * b.lambdaModel, 9);
    expect(b.lambdaFinal).toBeCloseTo(b.lambdaBase + 0.05, 9);
    expect(r.config.market?.ahLine).toBe(0.5);
  });

  it("U = R 时 r_att 与 m_def 严格等于 1,即使 R 里有人踢在非本位置(v0.2 第 2 条)", () => {
    const params = buildParams();
    params.players.h6.main_position = "CM";
    params.players.a2.main_position = "FB";
    const r = prepareMatch(params, setup());
    if (!r.ok) throw new Error(r.error);
    for (const t of r.config.teams) {
      expect(t.breakdown.rAtt).toBe(1);
      expect(t.breakdown.mDef).toBe(1);
    }
  });

  it("槽位分组只来自 position_map", () => {
    const params = buildParams();
    expect(slotGroup(params, "4-4-2", 74)).toBe("CM");
    expect(slotGroup(params, "4-4-2", 99)).toBeNull();
    params.teams["10"].last_lineup!.formation = "3-5-2";
    const r = prepareMatch(params, setup());
    expect(r.ok).toBe(false);
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

describe("校准参数注入(Phase 2)", () => {
  it("缺省时中性(HA = 1、κ 关闭);home_advantage=h 时主队 ×h、客队 ×1/h", () => {
    const base = prepareMatch(buildParams(), setup());
    if (!base.ok) throw new Error(base.error);
    const p = buildParams();
    p.leagues["1"].home_advantage = 1.2;
    const r = prepareMatch(p, setup());
    if (!r.ok) throw new Error(r.error);
    expect(r.config.teams[0].breakdown.lambdaModel).toBeCloseTo(base.config.teams[0].breakdown.lambdaModel * 1.2, 9);
    expect(r.config.teams[1].breakdown.lambdaModel).toBeCloseTo(base.config.teams[1].breakdown.lambdaModel / 1.2, 9);
    expect(base.config.kappa).toBe(0);
  });

  it("calibration.home_advantage 在联赛未给值时生效", () => {
    const base = prepareMatch(buildParams(), setup());
    if (!base.ok) throw new Error(base.error);
    const p = buildParams();
    p.calibration = { home_advantage: 1.1 };
    const r = prepareMatch(p, setup());
    if (!r.ok) throw new Error(r.error);
    expect(r.config.teams[0].breakdown.lambdaModel).toBeCloseTo(base.config.teams[0].breakdown.lambdaModel * 1.1, 9);
  });

  it("calibration 字段覆盖 w 与 κ", () => {
    const p = buildParams();
    p.calibration = { market_w: 1, kappa: 16 };
    const r = prepareMatch(p, setup({ fixtureId: 99 }));
    if (!r.ok) throw new Error(r.error);
    expect(r.config.kappa).toBe(16);
    expect(r.config.teams[0].breakdown.lambdaBase).toBeCloseTo(1.6 - 0.05, 9);
  });
});

describe("v0.3 强度回归(数据模型 λ)", () => {
  it("λ_model 总量 = exp(预测) − μ_OG,渠道占比不变,不再乘主场系数", () => {
    const p = buildParams();
    const feat = (a: number, d: number) => ({ n: 10, n_eff: 8.7, xg: { A: [a, a] as [number, number], D: [d, d] as [number, number] }, goals: { A: [a, a] as [number, number], D: [d, d] as [number, number] } });
    p.leagues["1"].goals_per_team_match = 1.4;
    p.teams["10"].strength = { "10": feat(1.8, 1.2) };
    p.teams["20"].strength = { "10": feat(1.2, 1.6) };
    const base = prepareMatch(p, setup());
    if (!base.ok) throw new Error(base.error);
    p.calibration = { strength_model: { window: "10", basis: "xg", k: 5, alpha: 0.3, gamma: { xg_A: 1.2, xg_D: 1.1 }, beta_home: 0.2 } };
    const r = prepareMatch(p, setup());
    if (!r.ok) throw new Error(r.error);
    const muXg = 0.8 + 0.15 + 0.35 + 0.1;
    const pred = Math.exp(0.3 + 0.2 + 1.2 * Math.log(1.8 / muXg) + 1.1 * Math.log(1.6 / muXg));
    expect(r.config.teams[0].breakdown.lambdaModel).toBeCloseTo(pred - 0.05, 9);
    const share = (x: typeof r.config.teams[0]) => x.lambda.open / (x.lambda.open + x.lambda.counter + x.lambda.setpiece + x.lambda.penalty);
    expect(share(r.config.teams[0])).toBeCloseTo(share(base.config.teams[0]), 9);
  });
});

describe("v0.3 时段 / 比分状态 / 红牌回归模式", () => {
  const zero = {
    periods: [0, 0, 0, 0, 0] as [number, number, number, number, number],
    early_state: { trail2: 0, trail1: 0, lead1: 0, lead2: 0 },
    late: { level: 0, trail1: 0, trail2: 0, lead1: 0, lead2: 0 },
    red_own_down: 0,
    red_opp_down: 0,
  };

  it("系数全为 0、事件全关时,每分钟 = λ/90:模拟进球 ≈ 期望进球 × 实际分钟 ÷ 90", () => {
    const p = buildParams();
    p.calibration = { rate_model: zero, kappa: 0 };
    const r = prepareMatch(p, setup({ effects: NO_EFFECTS }));
    if (!r.ok) throw new Error(r.error);
    expect(r.config.rateModel).not.toBeNull();
    const m = simulateMany(r.config, 5, 20000, [0, 0]);
    // 补时分布(见 buildParams):上半场均值 3.4,下半场均值 6.4 → 平均 99.8 分钟
    const minutes = 90 + (1 * 0.1 + 3 * 0.6 + 5 * 0.3) + (4 * 0.2 + 6 * 0.5 + 9 * 0.3);
    const expected = (r.config.teams[0].breakdown.expectedGoals + r.config.teams[1].breakdown.expectedGoals) * (minutes / 90);
    const ratio = (m.meanGoals[0] + m.meanGoals[1]) / expected;
    expect(ratio).toBeGreaterThan(0.99);
    expect(ratio).toBeLessThan(1.01);
  }, 60000);

  it("'75后 持平'系数为负时,末段进球占比下降", () => {
    const run = (level: number) => {
      const p = buildParams();
      p.calibration = { rate_model: { ...zero, late: { ...zero.late, level } }, kappa: 0 };
      const r = prepareMatch(p, setup({ effects: NO_EFFECTS }));
      if (!r.ok) throw new Error(r.error);
      const m = simulateMany(r.config, 5, 5000, [0, 0]);
      return m.goalBuckets[5] / m.goalBuckets.reduce((a, b) => a + b, 0);
    };
    expect(run(-0.5)).toBeLessThan(run(0));
  }, 60000);
});

describe("补时", () => {
  it("补时按分布抽样,落在分布支撑内", () => {
    const r = prepareMatch(buildParams(), setup());
    if (!r.ok) throw new Error(r.error);
    for (let seed = 1; seed <= 40; seed++) {
      const s = simulateOnce(r.config, seed);
      expect([1, 3, 5]).toContain(s.stoppage[0]);
      expect([4, 6, 9]).toContain(s.stoppage[1]);
      expect(s.totalTicks).toBe(90 + s.stoppage[0] + s.stoppage[1]);
    }
  });
});

describe("xG 同源", () => {
  it("赛跑图终点 = 实时累计 xG", () => {
    const r = prepareMatch(buildParams(), setup());
    if (!r.ok) throw new Error(r.error);
    const s = simulateOnce(r.config, 4242);
    const shots = toReportShots(s.events, [10, 20], s.halfTimeTick);
    const [h, a] = cumulativeXg(s.events);
    expect(cumulativeSeries(shots, true).at(-1)!.total).toBeCloseTo(h, 9);
    expect(cumulativeSeries(shots, false).at(-1)!.total).toBeCloseTo(a, 9);
    expect(shots.filter((x) => x.outcome === "Goal")).toHaveLength(s.score[0] + s.score[1]);
  });
});

describe("模拟事件 → xG 赛跑图", () => {
  it("客队进球转换成 is_home=false,并在图上用客队色;补时分钟随事件传出", () => {
    const r = prepareMatch(buildParams(), setup());
    if (!r.ok) throw new Error(r.error);
    let found = null as null | ReturnType<typeof simulateOnce>;
    for (let seed = 1; seed < 500 && !found; seed++) {
      const s = simulateOnce(r.config, seed);
      if (s.events.some((e) => e.kind === "goal" && e.team === 1 && e.channel !== "owngoal")) found = s;
    }
    if (!found) throw new Error("no away goal in 500 seeds");
    const shots = toReportShots(found.events, [10, 20], found.halfTimeTick);
    const awayGoals = shots.filter((x) => x.outcome === "Goal" && x.team_id === 20);
    expect(awayGoals.length).toBeGreaterThan(0);
    expect(awayGoals.every((x) => x.is_home === false)).toBe(true);
    const colors = { teal: "#087e78", navy: "#b45309", win: "#287851" } as ChartColors;
    const st = { firstHalf: found.stoppage[0], secondHalf: found.stoppage[1] };
    const opt = buildXgRaceOption(
      cumulativeSeries(shots, true, st), cumulativeSeries(shots, false, st), "主", "客",
      90 + st.firstHalf + st.secondHalf, "interactive", colors, st,
    );
    const away = (opt.series as { markPoint: { data: { itemStyle: { color: string } }[] } }[])[1];
    expect(away.markPoint.data.length).toBeGreaterThan(0);
    expect(away.markPoint.data.every((d) => d.itemStyle.color === "#b45309")).toBe(true);
    for (const e of found.events) expect(e.added).toBe(e.clock.includes("+") ? Number(e.clock.split("+")[1].replace("'", "")) : 0);
  });
});

describe("爆冷分级", () => {
  it("≥10% 常见、3%–10% 少见、<3% 罕见", () => {
    expect(rarityTag(100, 1000)).toBe("常见");
    expect(rarityTag(99, 1000)).toBe("少见");
    expect(rarityTag(30, 1000)).toBe("少见");
    expect(rarityTag(29, 1000)).toBe("罕见");
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
