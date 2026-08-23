import { describe, expect, it } from "vitest";
import {
  filterShotRows,
  outcomeLabel,
  summarizeShotRows,
} from "@/components/charts/ShotMapExplorer";

const data = {
  pitch: { length: 105, width: 68 },
  coordinate_note: "FotMob normalized attacking direction",
  window: 5,
  teams: [
    { team_id: 1, name: "主队", side: "home" },
    { team_id: 2, name: "客队", side: "away" },
  ],
  recent_sets: {
    "1": {
      same_league: { match_ids: [10], matched_games: 1, shot_covered_games: 1 },
      all_covered: { match_ids: [10, 11], matched_games: 2, shot_covered_games: 2 },
    },
  },
  matches: [],
  shots: [
    {
      shot_id: "a",
      match_id: 10,
      player_id: "p1",
      team_id: 1,
      minute: 10,
      period: "FirstHalf",
      x: 90,
      y: 30,
      xg: 0.2,
      outcome: "AttemptSaved",
      situation: "RegularPlay",
      shot_type: "RightFoot",
    },
    {
      shot_id: "b",
      match_id: 10,
      player_id: "p2",
      team_id: 2,
      minute: 20,
      period: "FirstHalf",
      x: 98,
      y: 34,
      xg: 0.4,
      outcome: "Goal",
      situation: "Penalty",
      shot_type: "LeftFoot",
    },
    {
      shot_id: "c",
      match_id: 11,
      player_id: "p3",
      team_id: 1,
      minute: 78,
      period: "SecondHalf",
      x: 80,
      y: 20,
      xg: 0.1,
      outcome: "Miss",
      situation: "RegularPlay",
      shot_type: "Header",
    },
  ],
} as Parameters<typeof filterShotRows>[0];

describe("recent shot-map filters", () => {
  it("按球队、创造/承受和真实覆盖范围过滤", () => {
    expect(filterShotRows(data, 1, "same_league", "created", "all").map((s) => s.shot_id)).toEqual([
      "a",
    ]);
    expect(filterShotRows(data, 1, "same_league", "conceded", "all").map((s) => s.shot_id)).toEqual([
      "b",
    ]);
    expect(filterShotRows(data, 1, "all_covered", "created", "all").map((s) => s.shot_id)).toEqual([
      "a",
      "c",
    ]);
  });

  it("射正只包含进球与被扑，不把偏出当射正", () => {
    expect(
      filterShotRows(data, 1, "all_covered", "created", "on_target").map((s) => s.shot_id),
    ).toEqual(["a"]);
    expect(
      filterShotRows(data, 1, "same_league", "conceded", "goal").map((s) => s.shot_id),
    ).toEqual(["b"]);
  });

  it("支持从近五场合计下钻到单场，不改变原始顺序", () => {
    expect(
      filterShotRows(data, 1, "all_covered", "created", "all", 11).map(
        (shot) => shot.shot_id,
      ),
    ).toEqual(["c"]);
    expect(
      filterShotRows(data, 1, "all_covered", "created", "all", 10).map(
        (shot) => shot.shot_id,
      ),
    ).toEqual(["a"]);
  });

  it("支持按射门方式、身体部位、上下半场筛选", () => {
    expect(
      filterShotRows(data, 1, "all_covered", "created", "all", undefined, ["Penalty"]).map(
        (s) => s.shot_id,
      ),
    ).toEqual([]);
    expect(
      filterShotRows(data, 1, "all_covered", "conceded", "all", undefined, ["Penalty"]).map(
        (s) => s.shot_id,
      ),
    ).toEqual(["b"]);
    expect(
      filterShotRows(data, 1, "all_covered", "created", "all", undefined, [], "Header").map(
        (s) => s.shot_id,
      ),
    ).toEqual(["c"]);
    expect(
      filterShotRows(data, 1, "all_covered", "created", "all", undefined, [], null, "second").map(
        (s) => s.shot_id,
      ),
    ).toEqual(["c"]);
    expect(
      filterShotRows(data, 1, "all_covered", "created", "all", undefined, [], null, "first").map(
        (s) => s.shot_id,
      ),
    ).toEqual(["a"]);
  });

  it("研报摘要只聚合当前筛选结果，xG 缺失不伪装为零", () => {
    expect(summarizeShotRows(data.shots)).toEqual({
      shots: 3,
      onTarget: 2,
      goals: 1,
      xg: 0.7,
    });
    expect(
      summarizeShotRows([
        {
          ...data.shots[0],
          shot_id: "missing-xg",
          xg: null,
          outcome: "Miss",
        },
      ]),
    ).toEqual({
      shots: 1,
      onTarget: 0,
      goals: 0,
      xg: null,
    });
  });

  it("is_blocked 已知时精确排除被封堵的球(2026-08-23 起才回填,is_on_target 本身不排除被封堵)", () => {
    const rows = [
      { ...data.shots[0], shot_id: "saved", outcome: "AttemptSaved", is_blocked: false },
      { ...data.shots[0], shot_id: "blocked", outcome: "AttemptSaved", is_blocked: true },
      { ...data.shots[0], shot_id: "goal", outcome: "Goal", is_blocked: false },
    ];
    const summary = summarizeShotRows(rows);
    // 3 脚里只有 2 脚真正射正(门将扑出的 1 + 进球 1),被封堵的 1 脚排除
    expect(summary.onTarget).toBe(2);
  });

  it("is_blocked 未知时(未回填场次)退回旧口径,不排除任何 AttemptSaved", () => {
    const rows = [
      { ...data.shots[0], shot_id: "unknown-1", outcome: "AttemptSaved", is_blocked: null },
      { ...data.shots[0], shot_id: "unknown-2", outcome: "AttemptSaved" },
    ];
    expect(summarizeShotRows(rows).onTarget).toBe(2);
  });
});

describe("outcomeLabel(2026-08-23 精确拆分被扑出/被封堵)", () => {
  it("is_blocked=false 标'被扑出',is_blocked=true 标'被封堵'", () => {
    expect(outcomeLabel("AttemptSaved", false)).toBe("被扑出");
    expect(outcomeLabel("AttemptSaved", true)).toBe("被封堵");
  });

  it("is_blocked 未提供或为 null 时退回'被扑/被挡'(不假装有精确度)", () => {
    expect(outcomeLabel("AttemptSaved")).toBe("被扑/被挡");
    expect(outcomeLabel("AttemptSaved", null)).toBe("被扑/被挡");
  });

  it("其它结果不受 is_blocked 影响", () => {
    expect(outcomeLabel("Goal", true)).toBe("进球");
    expect(outcomeLabel("Miss")).toBe("偏出");
    expect(outcomeLabel("Post")).toBe("击中门框");
  });
});
