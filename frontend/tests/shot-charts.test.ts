import { describe, expect, it } from "vitest";
import { buildBuckets } from "@/components/matches/ThreatTimeline";
import { cumulativeSeries } from "@/components/matches/XgRaceChart";
import { filterShots } from "@/components/matches/ShotMapChart";

type Shot = Parameters<typeof buildBuckets>[0][number];

function shot(partial: Partial<Shot>): Shot {
  return {
    player_id: "p1",
    player_name: "球员",
    team_id: 1,
    is_home: true,
    minute: 10,
    period: "FirstHalf",
    x: 90,
    y: 34,
    xg: 0.1,
    xgot: null,
    situation: "RegularPlay",
    outcome: "AttemptSaved",
    shot_type: "RightFoot",
    ...partial,
  } as Shot;
}

describe("buildBuckets(射门威胁时间轴)", () => {
  it("按时间段分别累加主客 xG,并单独收集进球", () => {
    const buckets = buildBuckets(
      [
        shot({ minute: 3, xg: 0.2, is_home: true }),
        shot({ minute: 4, xg: 0.5, is_home: true, outcome: "Goal", player_name: "甲" }),
        shot({ minute: 7, xg: 0.3, is_home: false }),
      ],
      5,
    );
    expect(buckets[0].homeXg).toBeCloseTo(0.7);
    expect(buckets[0].awayXg).toBe(0);
    expect(buckets[0].homeGoals.map((g) => g.player_name)).toEqual(["甲"]);
    expect(buckets[1].awayXg).toBeCloseTo(0.3);
  });

  it("排除点球大战(不属于比赛进程)", () => {
    const buckets = buildBuckets(
      [
        shot({ minute: 10, xg: 0.4 }),
        shot({ minute: 120, xg: 0.8, period: "PenaltyShootout", outcome: "Goal" }),
      ],
      5,
    );
    const total = buckets.reduce((a, b) => a + b.homeXg, 0);
    expect(total).toBeCloseTo(0.4);
    // 点球大战的进球也不得进入标记
    expect(buckets.some((b) => b.homeGoals.length > 0)).toBe(false);
  });

  it("xG 缺失按 0 计柱高,但射门与进球标记本身不丢(全库 0.3% 缺 xG)", () => {
    const buckets = buildBuckets(
      [shot({ minute: 12, xg: null, outcome: "Goal", player_name: "乙" })],
      5,
    );
    const b = buckets[2];
    expect(b.homeXg).toBe(0);
    expect(b.homeGoals.map((g) => g.player_name)).toEqual(["乙"]);
  });

  it("至少覆盖到 90 分钟,补时射门也有对应时段", () => {
    const buckets = buildBuckets([shot({ minute: 96, xg: 0.9 })], 5);
    expect(buckets[buckets.length - 1].start).toBeGreaterThanOrEqual(95);
    expect(buckets.reduce((a, b) => a + b.homeXg, 0)).toBeCloseTo(0.9);
  });

  it("完全没有可用射门时返回空数组(调用方渲染空态)", () => {
    expect(buildBuckets([], 5)).toEqual([]);
    expect(buildBuckets([shot({ minute: null })], 5)).toEqual([]);
  });
});

describe("cumulativeSeries(xG 累积对抗)", () => {
  it("按分钟升序累加,起点为 0", () => {
    const pts = cumulativeSeries(
      [
        shot({ minute: 30, xg: 0.3, is_home: true }),
        shot({ minute: 10, xg: 0.2, is_home: true }),
      ],
      true,
    );
    expect(pts[0]).toEqual({ minute: 0, total: 0, goal: null });
    expect(pts[1].minute).toBe(10);
    expect(pts[1].total).toBeCloseTo(0.2);
    expect(pts[2].total).toBeCloseTo(0.5);
  });

  it("只取指定一方的射门", () => {
    const shots = [
      shot({ minute: 10, xg: 0.4, is_home: true }),
      shot({ minute: 20, xg: 0.6, is_home: false }),
    ];
    expect(cumulativeSeries(shots, true).at(-1)?.total).toBeCloseTo(0.4);
    expect(cumulativeSeries(shots, false).at(-1)?.total).toBeCloseTo(0.6);
  });

  it("进球点被标出,非进球点为 null", () => {
    const pts = cumulativeSeries(
      [
        shot({ minute: 10, xg: 0.1, outcome: "Miss" }),
        shot({ minute: 20, xg: 0.7, outcome: "Goal", player_name: "丙" }),
      ],
      true,
    );
    expect(pts[1].goal).toBeNull();
    expect(pts[2].goal?.player_name).toBe("丙");
  });

  it("排除点球大战", () => {
    const pts = cumulativeSeries(
      [shot({ minute: 120, xg: 0.8, period: "PenaltyShootout", outcome: "Goal" })],
      true,
    );
    expect(pts).toEqual([{ minute: 0, total: 0, goal: null }]);
  });
});

describe("filterShots(射门图筛选)", () => {
  const shots = [
    shot({ minute: 5, is_home: true, outcome: "Goal", situation: "FromCorner", shot_type: "Header", period: "FirstHalf" }),
    shot({ minute: 20, is_home: true, outcome: "Miss", situation: "RegularPlay", shot_type: "LeftFoot", period: "FirstHalf" }),
    shot({ minute: 60, is_home: false, outcome: "AttemptSaved", situation: "FastBreak", shot_type: "RightFoot", period: "SecondHalf" }),
    shot({ minute: 70, is_home: false, outcome: "Post", situation: "FreeKick", shot_type: "RightFoot", period: "SecondHalf" }),
  ];

  it("按球队筛", () => {
    expect(filterShots(shots, base({ side: "home" }))).toHaveLength(2);
    expect(filterShots(shots, base({ side: "away" }))).toHaveLength(2);
  });

  it("射正 = 进球 + 被扑出(中框/偏出不算,与球队统计口径一致)", () => {
    const onTarget = filterShots(shots, base({ outcome: "on_target" }));
    expect(onTarget.map((s) => s.outcome).sort()).toEqual(["AttemptSaved", "Goal"]);
  });

  it("进球筛只留进球", () => {
    expect(filterShots(shots, base({ outcome: "goal" }))).toHaveLength(1);
  });

  it("情境多选是并集(角球 or 任意球)", () => {
    const r = filterShots(shots, base({ situations: ["FromCorner", "FreeKick"] }));
    expect(r).toHaveLength(2);
  });

  it("身体部位单选", () => {
    expect(filterShots(shots, base({ bodyPart: "RightFoot" }))).toHaveLength(2);
    expect(filterShots(shots, base({ bodyPart: "Header" }))).toHaveLength(1);
  });

  it("半场筛", () => {
    expect(filterShots(shots, base({ half: "first" }))).toHaveLength(2);
    expect(filterShots(shots, base({ half: "second" }))).toHaveLength(2);
  });

  it("多条件是交集,且互不冲突时可为空(调用方渲染空态)", () => {
    const r = filterShots(shots, base({ side: "home", half: "second" }));
    expect(r).toHaveLength(0);
  });

  it("默认不筛任何东西", () => {
    expect(filterShots(shots, base({}))).toHaveLength(shots.length);
  });
});

function base(
  over: Partial<Parameters<typeof filterShots>[1]>,
): Parameters<typeof filterShots>[1] {
  return {
    side: "both",
    outcome: "all",
    situations: [],
    bodyPart: null,
    half: "all",
    ...over,
  };
}
