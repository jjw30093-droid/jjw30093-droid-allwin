/**
 * playerQuadrantViews.ts(2026-09-15,联赛球员象限图):视角注册表约束、
 * playerPlotSet 的诚实披露(缺值/未达门槛两种隐藏原因不混淆)、
 * topPerQuadrant 的"每象限只画 10 人、均值不受截断影响"不变量。
 */

import { describe, expect, it } from "vitest";
import type { PlayerQuadrantRow } from "@/lib/api-v1";
import {
  PLAYER_VIEWS,
  playerHiddenNote,
  playerPlotSet,
  playerViewById,
  quadrantTruncationNote,
  topPerQuadrant,
} from "@/components/league/playerQuadrantViews";
import { mean } from "@/components/league/quadrantViews";

function row(pid: string, x: number | null, y: number | null, over: Partial<PlayerQuadrantRow> = {}): PlayerQuadrantRow {
  return {
    player: { player_id: pid, name: `球员${pid}`, name_en: null },
    team: { team_id: 1001, name: "队A", name_en: null, crest_url: null },
    usual_position: 2,
    appearances: 10,
    minutes_played: 900,
    team_minutes: 1800,
    minutes_share: 0.5,
    teams_count: 1,
    ratios: {
      chances_created_per90: x == null ? undefined : { value: x, numerator: x, denominator: 900, paired_matches: 10 },
      xa_per90: y == null ? undefined : { value: y, numerator: y, denominator: 900, paired_matches: 10 },
    },
    ...over,
  } as PlayerQuadrantRow;
}

describe("PLAYER_VIEWS 注册表约束", () => {
  it("5 个视角,id 唯一", () => {
    expect(PLAYER_VIEWS).toHaveLength(5);
    const ids = PLAYER_VIEWS.map((v) => v.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("四象限名非空且互不相同", () => {
    for (const v of PLAYER_VIEWS) {
      expect(v.quadrants).toHaveLength(4);
      expect(new Set(v.quadrants).size).toBe(4);
      for (const q of v.quadrants) expect(q.length).toBeGreaterThan(0);
    }
  });

  it("命名红线:防守贡献视角不得出现 PPDA/Field Tilt 字样(CBIRT 是行业标准术语,允许出现)", () => {
    for (const v of PLAYER_VIEWS) {
      expect(v.note).not.toMatch(/PPDA/);
      expect(v.note).not.toMatch(/Field Tilt/);
      expect(v.title).not.toMatch(/PPDA/);
    }
  });

  it("playerViewById 找不到时直接抛错,不静默回退", () => {
    expect(() => playerViewById("not-a-real-view")).toThrow();
  });

  it("每个视角的 note 都非空(CLAUDE.md §11.2 文字摘要义务)", () => {
    for (const v of PLAYER_VIEWS) expect(v.note.length).toBeGreaterThan(10);
  });
});

const view = playerViewById("player-creativity");

describe("playerPlotSet", () => {
  it("出场占比未达标的球员标记 below_threshold,不画出来", () => {
    const rows = [row("1", 1, 0.1), row("2", 2, 0.2, { minutes_share: 0.1 })];
    const { pts, hidden } = playerPlotSet(rows, view);
    expect(pts.map((p) => p.playerId)).toEqual(["1"]);
    expect(hidden).toEqual([{ key: "id:2", name: "球员2", axis: "x", reason: "below_threshold" }]);
  });

  it("数据源缺该指标的球员标记 missing,不画出来", () => {
    const rows = [row("1", 1, 0.1), row("2", null, 0.2)];
    const { pts, hidden } = playerPlotSet(rows, view);
    expect(pts.map((p) => p.playerId)).toEqual(["1"]);
    expect(hidden[0].reason).toBe("missing");
    expect(hidden[0].axis).toBe("x");
  });

  it("同一球员出现多行时不重复计入,后行可以救回前面标记的缺失", () => {
    const rows = [row("1", null, 0.1), row("1", 1, 0.2)];
    const { pts, hidden } = playerPlotSet(rows, view);
    expect(pts).toHaveLength(1);
    expect(hidden).toHaveLength(0);
  });

  it("mp 字段取自 appearances", () => {
    const rows = [row("1", 1, 0.1, { appearances: 7 })];
    const { pts } = playerPlotSet(rows, view);
    expect(pts[0].mp).toBe(7);
  });
});

describe("playerHiddenNote", () => {
  it("没有隐藏球员时返回空串", () => {
    expect(playerHiddenNote([])).toBe("");
  });

  it("below_threshold 和 missing 两种原因分别成句,不混为一谈", () => {
    const note = playerHiddenNote([
      { key: "id:1", name: "甲", axis: "x", reason: "below_threshold" },
      { key: "id:2", name: "乙", axis: "y", reason: "missing" },
    ]);
    expect(note).toContain("出场时间不足本队已踢时间的 40%");
    expect(note).toContain("甲");
    expect(note).toContain("数据源缺这两项之一");
    expect(note).toContain("乙");
    expect(note).toContain("虚线平均值只统计画出的球员");
  });
});

describe("topPerQuadrant", () => {
  it("每象限最多画 take(默认10)个,不足时全画", () => {
    // 造 3 个象限各只有 2 个点(总共 6 个,均 < 10),验证不截断
    const pts = [
      { key: "a", name: "a", x: 1, y: 1, mp: 1, avatarUrl: null, playerId: "a", teamName: "T", teamCrestUrl: null },
      { key: "b", name: "b", x: 2, y: 2, mp: 1, avatarUrl: null, playerId: "b", teamName: "T", teamCrestUrl: null },
      { key: "c", name: "c", x: -1, y: 1, mp: 1, avatarUrl: null, playerId: "c", teamName: "T", teamCrestUrl: null },
    ];
    const mx = mean(pts.map((p) => p.x));
    const my = mean(pts.map((p) => p.y));
    const { drawn, totalByQuadrant } = topPerQuadrant(pts, mx, my, {});
    expect(drawn).toHaveLength(3);
    expect(totalByQuadrant.reduce((a, b) => a + b, 0)).toBe(3);
  });

  it("象限内人数超过 take 时只画离均值最远的 take 个", () => {
    // 11 个点紧密聚在一起(x=100..110),外加 1 个远离的孤点(x=0,y=0)把均值
    // 拉到约 91.7——11 个聚集点仍然全部 >= 均值,全落进同一象限(超过 take=10,
    // 触发截断);孤点自己落进相反象限(只有 1 个,不触发截断)。
    const clustered = Array.from({ length: 11 }, (_, i) => ({
      key: `c${i}`, name: `c${i}`, x: 100 + i, y: 100 + i,
      mp: 1, avatarUrl: null, playerId: `c${i}`, teamName: "T", teamCrestUrl: null,
    }));
    const outlier = {
      key: "o", name: "o", x: 0, y: 0,
      mp: 1, avatarUrl: null, playerId: "o", teamName: "T", teamCrestUrl: null,
    };
    const pts = [...clustered, outlier];
    const mx = mean(pts.map((p) => p.x));
    const my = mean(pts.map((p) => p.y));
    const { drawn, totalByQuadrant } = topPerQuadrant(pts, mx, my, {}, 10);
    expect(drawn).toHaveLength(11); // 10(截断后的聚集组) + 1(孤点独占的象限)
    expect(totalByQuadrant.some((n) => n > 10)).toBe(true);
    // 聚集组里离均值最远的是 c10(x=110),最近的是 c0(x=100)——10 个名额
    // 应该优先保留离均值更远的,c0 被挤掉。
    const names = drawn.map((p) => p.name);
    expect(names).toContain("c10");
    expect(names).not.toContain("c0");
    expect(names).toContain("o");
  });

  it("均值不受截断影响——mx/my 是纯参数,截断只发生在 drawn 上,totalByQuadrant 记录的是截断前的真实人数", () => {
    const clustered = Array.from({ length: 11 }, (_, i) => ({
      key: `c${i}`, name: `c${i}`, x: 100 + i, y: 100 + i,
      mp: 1, avatarUrl: null, playerId: `c${i}`, teamName: "T", teamCrestUrl: null,
    }));
    const outlier = {
      key: "o", name: "o", x: 0, y: 0,
      mp: 1, avatarUrl: null, playerId: "o", teamName: "T", teamCrestUrl: null,
    };
    const pts = [...clustered, outlier];
    const mx = mean(pts.map((p) => p.x));
    const my = mean(pts.map((p) => p.y));
    const { totalByQuadrant } = topPerQuadrant(pts, mx, my, {}, 10);
    // 截断只影响 drawn(11→10),不影响 totalByQuadrant——它必须仍反映
    // 截断前的真实分布(11 + 1 = 12),供 quadrantTruncationNote 如实披露。
    expect(totalByQuadrant.reduce((a, b) => a + b, 0)).toBe(12);
  });
});

describe("quadrantTruncationNote", () => {
  it("没有象限超过 take 时返回空串", () => {
    expect(quadrantTruncationNote([1, 2, 3, 4])).toBe("");
  });

  it("有象限超过 take 时给出披露文案", () => {
    const note = quadrantTruncationNote([15, 2, 3, 4]);
    expect(note).toContain("每象限只画离均值最远的 10 人");
    expect(note).toContain("不受这条截断影响");
  });
});
