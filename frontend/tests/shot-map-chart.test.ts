import { describe, expect, it } from "vitest";
import {
  filterShots,
  trajectoryEndpoint,
  resolveSelectedShot,
  buildShotMapSummary,
  summarizeSide,
} from "@/components/matches/ShotMapChart";
import type { MatchReportResponse } from "@/lib/api-v1";

type MatchReport = Extract<MatchReportResponse, { available: true }>;
type Shot = MatchReport["shots"][number];

function shot(overrides: Partial<Shot>): Shot {
  return {
    player_id: "p1",
    player_name: "球员",
    team_id: 1,
    is_home: true,
    minute: 10,
    period: "FirstHalf",
    x: 90,
    y: 34,
    xg: 0.3,
    xgot: null,
    situation: "RegularPlay",
    outcome: "Goal",
    shot_type: "RightFoot",
    is_blocked: null,
    is_on_target: null,
    is_own_goal: false,
    is_own_goal_inferred: false,
    ...overrides,
  };
}

const BASE_FILTER = { side: "both" as const, outcome: "on_target" as const, situations: [], bodyPart: null, half: "all" as const };

describe("filterShots 射正筛选(2026-08-23 起 is_blocked 已知时精确排除被封堵的球)", () => {
  it("is_blocked=true 的 AttemptSaved 不算射正,is_blocked=false 的算", () => {
    const shots = [
      shot({ outcome: "AttemptSaved", is_blocked: false }),
      shot({ outcome: "AttemptSaved", is_blocked: true }),
      shot({ outcome: "Goal", is_blocked: false }),
    ];
    const result = filterShots(shots, BASE_FILTER);
    expect(result).toHaveLength(2);
    expect(result.every((s) => !(s.outcome === "AttemptSaved" && s.is_blocked))).toBe(true);
  });

  it("is_blocked 未回填(null)时退回旧口径,不排除任何 AttemptSaved", () => {
    const shots = [
      shot({ outcome: "AttemptSaved", is_blocked: null }),
      shot({ outcome: "Miss" }),
    ];
    const result = filterShots(shots, BASE_FILTER);
    expect(result).toHaveLength(1);
    expect(result[0].outcome).toBe("AttemptSaved");
  });

  it("Miss/Post 从不算射正,不论 is_blocked", () => {
    const shots = [shot({ outcome: "Miss", is_blocked: false }), shot({ outcome: "Post" })];
    expect(filterShots(shots, BASE_FILTER)).toHaveLength(0);
  });
});

describe("trajectoryEndpoint(2026-08-24,射门轨迹线终点)", () => {
  it("is_blocked 且 blocked_x/blocked_y 均非空 → 返回封堵点,blocked=true", () => {
    const s = shot({ outcome: "AttemptSaved", is_blocked: true, blocked_x: 81.13, blocked_y: 33.16 });
    expect(trajectoryEndpoint(s)).toEqual({ x: 81.13, y: 33.16, blocked: true });
  });

  it("is_blocked=true 但 blocked_x/blocked_y 缺失 → 不使用封堵分支,按进球/射正退化", () => {
    const s = shot({ outcome: "Goal", is_blocked: true, blocked_x: null, blocked_y: null });
    expect(trajectoryEndpoint(s)).toEqual({ x: 105, y: 34, blocked: false });
  });

  it("进球(outcome==='Goal')且无精确终点数据 → 退化到球门正中,blocked=false", () => {
    const s = shot({ outcome: "Goal", is_blocked: null });
    expect(trajectoryEndpoint(s)).toEqual({ x: 105, y: 34, blocked: false });
  });

  it("is_on_target===true 且无精确终点数据 → 同样退化到球门正中", () => {
    const s = shot({ outcome: "AttemptSaved", is_on_target: true, is_blocked: false });
    expect(trajectoryEndpoint(s)).toEqual({ x: 105, y: 34, blocked: false });
  });

  it("未被封堵、非射正的球(如 Miss)→ 没有可信终点,返回 null", () => {
    const s = shot({ outcome: "Miss", is_blocked: null, is_on_target: false });
    expect(trajectoryEndpoint(s)).toBeNull();
  });
});

describe("resolveSelectedShot(2026-08-24,筛选变化后选中态的边界处理)", () => {
  it("选中项在 plotted 里时原样返回", () => {
    const a = shot({ player_id: "a" });
    const b = shot({ player_id: "b" });
    expect(resolveSelectedShot([a, b], a)).toBe(a);
  });

  it("选中项不在当前 plotted 里(切筛选筛掉了)时返回 null", () => {
    const a = shot({ player_id: "a" });
    const b = shot({ player_id: "b" });
    expect(resolveSelectedShot([b], a)).toBeNull();
  });

  it("selected 本身为 null 时返回 null,不抛异常", () => {
    expect(resolveSelectedShot([shot({})], null)).toBeNull();
  });
});

describe("buildShotMapSummary / summarizeSide(2026-08-24,摘要聚合纯函数化)", () => {
  // 此前摘要三个数字只活在组件渲染路径里,frontend/tests 全目录 grep
  // 「次射门」零命中——正确性完全靠人肉维持(CLAUDE.md §11.3 新增纪律的
  // 直接动因)。以下断言:①三个数字与筛选集合同源;②乌龙球按受益方计球;
  // ③缺失 xG 不静默当 0。

  it("摘要随筛选集合变化——筛进球后次数/球数/xG 同步缩小", () => {
    const all = [
      shot({ player_id: "h1", is_home: true, outcome: "Goal", xg: 0.5 }),
      shot({ player_id: "h2", is_home: true, outcome: "Miss", xg: 0.1 }),
      shot({ player_id: "a1", is_home: false, outcome: "Goal", xg: 0.3 }),
    ];
    const goalsOnly = all.filter((s) => s.outcome === "Goal");
    const full = buildShotMapSummary({
      plotted: all, plottableCount: 3, shootout: 0, homeName: "主", awayName: "客",
    });
    const filtered = buildShotMapSummary({
      plotted: goalsOnly, plottableCount: 3, shootout: 0, homeName: "主", awayName: "客",
    });
    expect(full).toContain("主(攻向右)2 次射门、1 球、射门图 xG 合计 0.60");
    expect(filtered).toContain("主(攻向右)1 次射门、1 球、射门图 xG 合计 0.50");
    expect(filtered).toContain("当前按筛选条件显示 2/3 次射门");
    expect(full).not.toContain("当前按筛选条件");
  });

  it("乌龙球按受益方计球:客队球员乌龙 → 主队球数 +1,客队不计", () => {
    const plotted = [
      shot({ player_id: "h1", is_home: true, outcome: "Goal", xg: 0.4 }),
      // 客队球员打进自家球门:FotMob 记在客队名下(is_home=false),xG 缺失
      shot({ player_id: "a-og", is_home: false, outcome: "Goal", xg: null,
             is_own_goal: true, is_own_goal_inferred: true }),
    ];
    const h = summarizeSide(plotted, true);
    const a = summarizeSide(plotted, false);
    expect(h.goals).toBe(2);          // 1 正常进球 + 1 受益乌龙
    expect(h.ownGoalsBenefited).toBe(1);
    expect(a.goals).toBe(0);          // 自己的乌龙不给自己计球
    expect(a.n).toBe(1);              // 但该点画在客队侧,次数如实
    const text = buildShotMapSummary({
      plotted, plottableCount: 2, shootout: 0, homeName: "主", awayName: "客",
    });
    expect(text).toContain("主(攻向右)1 次射门、2 球");
    expect(text).toContain("客(攻向左)1 次射门、0 球");
    expect(text).toContain("其中 1 球为乌龙球");
  });

  it("缺失 xG 不当 0:乌龙球缺 xG 不触发异常提示,正常射门缺 xG 才提示", () => {
    const ogOnly = [
      shot({ is_home: false, outcome: "Goal", xg: null, is_own_goal: true }),
    ];
    const t1 = buildShotMapSummary({
      plotted: ogOnly, plottableCount: 1, shootout: 0, homeName: "主", awayName: "客",
    });
    expect(t1).not.toContain("缺少 xG 数据");
    expect(t1).toContain("客(攻向左)1 次射门、0 球、射门图 xG 合计 —");

    const missing = [shot({ is_home: true, outcome: "Miss", xg: null })];
    const t2 = buildShotMapSummary({
      plotted: missing, plottableCount: 1, shootout: 0, homeName: "主", awayName: "客",
    });
    expect(t2).toContain("另有 1 次射门缺少 xG 数据");
    expect(t2).toContain("主(攻向右)1 次射门、0 球、射门图 xG 合计 —");
  });

  it("零射门侧 xG 显示 0.00(不是 —)", () => {
    const onlyHome = [shot({ is_home: true, outcome: "Miss", xg: 0.2 })];
    const t = buildShotMapSummary({
      plotted: onlyHome, plottableCount: 1, shootout: 0, homeName: "主", awayName: "客",
    });
    expect(t).toContain("客(攻向左)0 次射门、0 球、射门图 xG 合计 0.00");
  });
});
