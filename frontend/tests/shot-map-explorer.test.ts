import { describe, expect, it } from "vitest";
import {
  filterShotRows,
  officialOnTargetApplies,
  officialOnTargetSum,
} from "@/components/charts/ShotMapExplorer";

// 最小可用的 ShotMapData fixture:两支球队各打了两场,team 1 主场对 team 2、
// team 1 客场对 team 3。official_stats 覆盖三支球队在这几场的官方口径。
const data = {
  pitch: { length: 105, width: 68 },
  coordinate_note: "",
  window: 5,
  teams: [
    { team_id: 1, name: "队A", side: "home" },
    { team_id: 2, name: "队B", side: "away" },
  ],
  recent_sets: {},
  matches: [
    { match_id: 100, date_utc: "2026-01-01", home: { team_id: 1, name: "队A" }, away: { team_id: 2, name: "队B" } },
    { match_id: 101, date_utc: "2026-01-08", home: { team_id: 3, name: "队C" }, away: { team_id: 1, name: "队A" } },
    { match_id: 102, date_utc: "2026-01-15", home: { team_id: 1, name: "队A" }, away: { team_id: 4, name: "队D" } },
  ],
  shots: [],
  official_stats: [
    { match_id: 100, team_id: 1, shots_on_target: 5, blocked_shots: 2, total_shots: 10 },
    { match_id: 100, team_id: 2, shots_on_target: 3, blocked_shots: 1, total_shots: 8 },
    { match_id: 101, team_id: 3, shots_on_target: 4, blocked_shots: 3, total_shots: 12 },
    { match_id: 101, team_id: 1, shots_on_target: 2, blocked_shots: 0, total_shots: 6 },
    // 102 场故意不给官方数据,模拟 fact_team_match_stats 缺失
  ],
};

describe("officialOnTargetSum(官方口径射正)", () => {
  it("created 视角:直接按 teamId 汇总本队射正", () => {
    const r = officialOnTargetSum(data, [100, 101], 1, "created");
    // 队A 在 100 场 5 + 101 场 2 = 7,均有官方数据
    expect(r).toEqual({ value: 7, covered: 2, total: 2 });
  });

  it("conceded 视角:按每场对手 team_id 取值,不是固定队伍", () => {
    // 队A 视角"对手射正":100 场对手是队B(3),101 场对手是队C(4)
    const r = officialOnTargetSum(data, [100, 101], 1, "conceded");
    expect(r).toEqual({ value: 7, covered: 2, total: 2 });
  });

  it("部分场次缺官方数据:未覆盖的场次不计入总和,但计入 total", () => {
    const r = officialOnTargetSum(data, [100, 101, 102], 1, "created");
    expect(r.value).toBe(7); // 102 场没有官方数据,不贡献
    expect(r.covered).toBe(2);
    expect(r.total).toBe(3);
  });

  it("完全没有官方数据的场次集合:value 为 null,不是 0", () => {
    // 0 是"官方统计射正为 0"的真实值,不能用来表示"没有数据"
    const r = officialOnTargetSum(data, [102], 1, "created");
    expect(r).toEqual({ value: null, covered: 0, total: 1 });
  });

  it("空场次列表:value 为 null", () => {
    expect(officialOnTargetSum(data, [], 1, "created")).toEqual({
      value: null,
      covered: 0,
      total: 0,
    });
  });

  it("单场(selectedMatchId 场景)按同样逻辑工作", () => {
    const r = officialOnTargetSum(data, [100], 1, "created");
    expect(r).toEqual({ value: 5, covered: 1, total: 1 });
  });
});

describe("officialOnTargetApplies(官方射正是否可用于当前筛选)", () => {
  it("全默认(无任何子筛选)时官方口径可用", () => {
    expect(officialOnTargetApplies("all", "all", [], null)).toBe(true);
  });

  it("结果筛选(射正/进球)生效时不可用", () => {
    expect(officialOnTargetApplies("on_target", "all", [], null)).toBe(false);
    expect(officialOnTargetApplies("goal", "all", [], null)).toBe(false);
  });

  it("半场筛选生效时不可用——官方统计没有半场维度", () => {
    expect(officialOnTargetApplies("all", "first", [], null)).toBe(false);
    expect(officialOnTargetApplies("all", "second", [], null)).toBe(false);
  });

  it("射门情境筛选生效时不可用——官方统计没有情境维度", () => {
    expect(officialOnTargetApplies("all", "all", ["Penalty"], null)).toBe(false);
  });

  it("身体部位筛选生效时不可用——官方统计没有身体部位维度", () => {
    expect(officialOnTargetApplies("all", "all", [], "Header")).toBe(false);
  });

  it("多个子筛选同时生效仍不可用", () => {
    expect(officialOnTargetApplies("on_target", "first", ["Penalty"], "Header")).toBe(false);
  });
});

describe("filterShotRows 排除点球大战(2026-08-23 对齐 ShotMapChart.tsx 口径)", () => {
  it("PenaltyShootout 的射门不计入,即使命中球队/场次筛选", () => {
    const withShootout = {
      ...data,
      recent_sets: { "1": { same_league: { matched_games: 2, match_ids: [100, 101] } } },
      shots: [
        { match_id: 100, team_id: 1, period: "FirstHalf", outcome: "Goal", x: 90, y: 34, xg: 0.5, minute: 10, situation: "RegularPlay", shot_type: "RightFoot" },
        { match_id: 100, team_id: 1, period: "PenaltyShootout", outcome: "Goal", x: 100, y: 34, xg: null, minute: null, situation: "Penalty", shot_type: "RightFoot" },
      ],
    };
    const rows = filterShotRows(withShootout as never, 1, "same_league", "created", "all");
    expect(rows).toHaveLength(1);
    expect(rows[0].period).toBe("FirstHalf");
  });
});
