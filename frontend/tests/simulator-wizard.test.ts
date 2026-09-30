import { describe, expect, it } from "vitest";
import { prepareMatch, type MatchSetup, type SlotAssign, type TeamSetup } from "@/features/simulator/engine";
import { applyPick } from "@/features/simulator/formation";
import { matchesPlayerQuery, pickerRows } from "@/features/simulator/picker";
import { slotBadge } from "@/features/simulator/slotBadge";
import type { PlayerParams, PosGroup, SimParams } from "@/features/simulator/types";
import { emptySlots, lineupIssue, marketStatus, pairWithAway, pairWithHome, stepForQuery } from "@/features/simulator/wizard";

// 与 simulator-engine.test.ts 同构的最小参数:4-4-2,主队 10(h*)、客队 20(a*)
const SLOTS: [number, PosGroup][] = [
  [11, "GK"], [32, "FB"], [34, "CB"], [36, "CB"], [38, "FB"], [72, "W"], [74, "CM"], [76, "CM"], [78, "W"], [104, "ST"], [106, "ST"],
];

function player(id: string, main: PosGroup, over: Partial<PlayerParams> = {}): PlayerParams {
  return {
    player_id: id, name_zh: `球员${id}`, name_en: `Player ${id}`, shirt_number: id.replace(/\D/g, "") || "1", team_id: id.startsWith("h") ? 10 : 20,
    usual_position_id: 1, top_formation: "4-4-2", top_position_id: 32, top_position_starts: 5, starts: 5, main_position: main,
    position_source: "rule_decoded", position_unverified: true, minutes: 900, a_p: 0.1, npxg90: 0.2, xa90: 0.1, shots90: 1, headers90: 0,
    penalties_taken: 0, penalties_scored: 0, r_p: 5, g_p: main === "GK" ? 0 : null, ...over,
  } as PlayerParams;
}

function params(): SimParams {
  const q = (lo: number, hi: number) => Array.from({ length: 19 }, (_, i) => lo + ((hi - lo) * i) / 18);
  const players: Record<string, PlayerParams> = {};
  for (const prefix of ["h", "a"]) {
    SLOTS.forEach(([, g], i) => (players[`${prefix}${i}`] = player(`${prefix}${i}`, g)));
    players[`${prefix}gk2`] = player(`${prefix}gk2`, "GK", { minutes: 180, name_zh: "替补门将" });
    players[`${prefix}cb2`] = player(`${prefix}cb2`, "CB", { minutes: 1500, name_zh: "替补中卫", name_en: "Backup CB", shirt_number: "44" });
    players[`${prefix}st2`] = player(`${prefix}st2`, "ST", { minutes: 30, name_zh: "青训前锋" });
  }
  const team = (id: number, prefix: string) => ({
    team_id: id, league_id: 1, name_zh: `队${id}`, A: { open: 1, counter: 0.2, setpiece: 0.4, penalty: 0.1 }, D: { open: 0.7, counter: 0.1, setpiece: 0.3, penalty: 0.1 },
    last_lineup: { match_id: 1, date: "2026-09-19", formation: "4-4-2", starters: SLOTS.map(([pid], i) => ({ player_id: `${prefix}${i}`, position_id: pid })) },
    squad: [...SLOTS.map((_, i) => `${prefix}${i}`), `${prefix}gk2`, `${prefix}cb2`, `${prefix}st2`],
  });
  return {
    meta: { generated_at: "2026-09-29T00:00:00Z", model_version: "v0.3", uncalibrated: true },
    leagues: {
      "1": {
        league_id: 1, finished_matches: 400,
        mu: { open: 0.8, counter: 0.15, setpiece: 0.35, penalty: 0.1, owngoal: 0.05 },
        shot_xg_quantiles: { probs: [], open: q(0.02, 0.4), counter: q(0.03, 0.5), setpiece: q(0.02, 0.35), penalty: q(0.79, 0.79) },
        shot_xg_mean: { open: 0.1, counter: 0.16, setpiece: 0.1, penalty: 0.79 },
        red_card_rate: 0.06, penalty_rate: 0.12, penalty_conversion: 0.8,
        goal_timing: { buckets: [], goals: [], factor: [1, 1, 1, 1, 1, 1] },
        stoppage_mean: { first_half: 3, second_half: 6 },
        stoppage_distribution: { first_half: { "3": 1 }, second_half: { "6": 1 } },
        gk_xgot_faced_per90: 1.35,
      },
    },
    formations: { "4-4-2": { samples: 10, slots: SLOTS.map(([pid]) => ({ position_id: pid, x: 0.5, y: 0.5 })) } },
    position_map: { "4-4-2": Object.fromEntries(SLOTS.map(([pid, g]) => [String(pid), g])) },
    teams: { "10": team(10, "h"), "20": team(20, "a"), "30": team(30, "a") },
    players,
    fixtures: {},
  } as unknown as SimParams;
}

function slotsFor(prefix: string, override: Record<number, string | null> = {}): SlotAssign[] {
  return SLOTS.map(([pid, g], i) => ({ positionId: pid, group: g, playerId: i in override ? override[i] : `${prefix}${i}`, x: 0.5, y: 0.5 }));
}
function setup(over: Partial<TeamSetup> = {}): TeamSetup {
  return { teamId: 10, formation: "4-4-2", slots: slotsFor("h"), focuses: [], shortRest: false, ...over };
}
const FULL = "?m=v0.3&pd=x&lg=1&s=1&c=0&h=10&hf=4-4-2&hx=" + SLOTS.map(([pid], i) => `${pid}:h${i}`).join(",") + "&hk=&hr=0&a=20&af=4-4-2&ax=" + SLOTS.map(([pid], i) => `${pid}:a${i}`).join(",") + "&ak=&ar=0";

describe("向导:初始步骤", () => {
  const p = params();
  it("空链接 / 只带 lg → 第 1 步", () => {
    expect(stepForQuery("", p, 1)).toBe(1);
    expect(stepForQuery("?lg=1", p, 1)).toBe(1);
  });
  it("只带两队(比赛页入口)→ 第 2 步;球队不在参数里 / 主客同队 / 联赛不符 → 第 1 步", () => {
    expect(stepForQuery("?lg=1&h=10&a=20&fx=5", p, 1)).toBe(2);
    expect(stepForQuery("?lg=1&h=10&a=99", p, 1)).toBe(1);
    expect(stepForQuery("?lg=1&h=10&a=10", p, 1)).toBe(1);
    expect(stepForQuery("?lg=2&h=10&a=20", p, 1)).toBe(1);
  });
  it("完整分享设定 → 第 2 步", () => {
    expect(stepForQuery(FULL, p, 1)).toBe(2);
    expect(stepForQuery(FULL, p, 2)).toBe(1);
  });
});

describe("向导:换队撞队", () => {
  const ids = [10, 20, 30];
  it("换主队与客队相同时,客队改为第一支不同的队;否则客队不动", () => {
    expect(pairWithHome([10, 20], 20, ids)).toEqual([20, 10]);
    expect(pairWithHome([10, 20], 30, ids)).toEqual([30, 20]);
  });
  it("换客队对称", () => {
    expect(pairWithAway([10, 20], 10, ids)).toEqual([20, 10]);
    expect(pairWithAway([10, 20], 30, ids)).toEqual([10, 30]);
  });
});

describe("向导:阵容完整性与 prepareMatch 对拍", () => {
  const p = params();
  const away = setup({ teamId: 20, slots: slotsFor("a") });
  const cases: [string, TeamSetup][] = [
    ["完整", setup()],
    ["空位", setup({ slots: slotsFor("h", { 3: null, 7: null }) })],
    ["重复", setup({ slots: slotsFor("h", { 3: "h2" }) })],
    ["无门将", setup({ slots: slotsFor("h", { 0: "hcb2" }) })],
  ];
  for (const [name, home] of cases) {
    it(name, () => {
      const issue = lineupIssue(p, home);
      const ms: MatchSetup = { leagueId: 1, home, away, fixtureId: null, chaos: false };
      expect(issue != null).toBe(!prepareMatch(p, ms).ok);
    });
  }
  it("空位文案含数量,空位下标可标出", () => {
    const s = setup({ slots: slotsFor("h", { 3: null, 7: null }) });
    expect(lineupIssue(params(), s)).toContain("2 个位置");
    expect(emptySlots(s)).toEqual([3, 7]);
  });
});

describe("选人面板:候选与排序", () => {
  const p = params();
  it("同位置组优先(推荐),其余按出场时间降序;当前球员排除;首发标记位置", () => {
    const r = pickerRows(p, setup(), 2); // CB 位,当前 h2
    expect(r.current?.player_id).toBe("h2");
    expect(r.suggested.map((x) => x.player.player_id)).toEqual(["hcb2", "h3"]); // 1500 分钟 > 900
    expect(r.suggested.find((x) => x.player.player_id === "h3")?.inXiSlot).toBe(3);
    expect(r.suggested.find((x) => x.player.player_id === "hcb2")?.inXiSlot).toBeNull();
    expect(r.others[0].fit).toBeLessThan(1);
    expect(r.others.every((x) => x.player.player_id !== "h2")).toBe(true);
  });
  it("出场不足 90 分钟默认隐藏并计数;显示全部或有搜索词时不隐藏", () => {
    expect(pickerRows(p, setup(), 9).hiddenLowMinutes).toBe(1);
    expect(pickerRows(p, setup(), 9, { showAll: true }).hiddenLowMinutes).toBe(0);
    const q = pickerRows(p, setup(), 9, { query: "青训" });
    expect(q.hiddenLowMinutes).toBe(0);
    expect(q.suggested.map((x) => x.player.player_id)).toEqual(["hst2"]);
  });
  it("搜索:中文 / 英文不分大小写 / 号码精确 / 前后空格", () => {
    const cb = p.players.hcb2;
    expect(matchesPlayerQuery(cb, "替补")).toBe(true);
    expect(matchesPlayerQuery(cb, "backup")).toBe(true);
    expect(matchesPlayerQuery(cb, " 44 ")).toBe(true);
    expect(matchesPlayerQuery(cb, "4")).toBe(false);
    expect(matchesPlayerQuery(cb, "门将")).toBe(false);
  });
});

describe("选人面板:选中后的阵容", () => {
  it("替补换上;首发则互换", () => {
    const s = setup();
    expect(applyPick(s, 2, "hcb2").slots[2].playerId).toBe("hcb2");
    const swapped = applyPick(s, 2, "h9");
    expect(swapped.slots[2].playerId).toBe("h9");
    expect(swapped.slots[9].playerId).toBe("h2");
  });
});

describe("球场徽标", () => {
  const cm = player("h6", "CM", { minutes: 1234, npxg90: 0.37 });
  it("位置:同组显示组名,错配显示折扣并警示", () => {
    expect(slotBadge(cm, "CM", "position")).toEqual({ text: "CM", warn: false, quiet: true });
    expect(slotBadge(cm, "AM", "position")).toEqual({ text: "×0.9", long: "CM→AM ×0.9", warn: true });
  });
  it("出场时间与场均 xG;xG 缺失显示 — 而不是 0", () => {
    expect(slotBadge(cm, "CM", "minutes")).toEqual({ text: "1234'", warn: false });
    expect(slotBadge(cm, "CM", "xg")?.text).toBe("0.37");
    expect(slotBadge(player("x", "CM", { npxg90: null }), "CM", "xg")?.text).toBe("—");
    expect(slotBadge(player("x", "CM", { npxg90: 0 }), "CM", "xg")?.text).toBe("0.00");
    expect(slotBadge(null, "CM", "xg")).toBeNull();
  });
});

describe("盘口状态行", () => {
  const p = params();
  (p as unknown as { fixtures: Record<string, { status: string }> }).fixtures = { "1": { status: "未开赛" }, "2": { status: "已完赛（赛前盘口）" } };
  it("匹配 / 关掉参考 / 没有盘口 三种情况都有一行字", () => {
    expect(marketStatus(p, 1, true)).toBe("已参考本场盘口(未开赛)");
    expect(marketStatus(p, 2, true)).toBe("已参考本场赛前盘口(比赛已踢完)");
    expect(marketStatus(p, null, true)).toContain("当前未参考");
    expect(marketStatus(p, null, false)).toBe("这对球队近期没有盘口数据,只用数据模型");
  });
});
