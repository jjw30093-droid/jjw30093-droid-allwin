/**
 * plotSet / hiddenNote(2026-09-14)。样本门槛开启后,被藏起来的球队必须能被
 * 数出来、说清楚为什么——"缺数据"和"样本不够"是两种不同的诚实,不能混淆。
 */

import { describe, expect, it } from "vitest";
import { METRICS } from "@/components/league/teamMetrics";
import { collectPoints, hiddenNote, plotSet, type View } from "@/components/league/quadrantViews";
import type { TeamSeasonStatRow } from "@/lib/api-v1";

function row(id: number, name: string, over: Partial<TeamSeasonStatRow> = {}): TeamSeasonStatRow {
  return {
    team: { team_id: id, name, name_en: null, crest_url: null },
    matches_played: 10,
    avg_expected_goals: 1.4,
    avg_expected_goals_conceded: 1.2,
    ...over,
  } as TeamSeasonStatRow;
}

const view = { x: METRICS.xg, y: METRICS.xga };

describe("plotSet", () => {
  it("缺一项数据的球队记为 missing,不是 sample", () => {
    const rows = [row(1, "A"), row(2, "B", { avg_expected_goals: null })];
    const { pts, hidden } = plotSet(rows, view);
    expect(pts.map((p) => p.name)).toEqual(["A"]);
    expect(hidden).toEqual([{ key: "id:2", name: "B", axis: "x", reason: "missing" }]);
  });

  it("样本不足的球队记为 sample,axis 指向不达标的那根轴", () => {
    const gated: View["x"] = {
      ...METRICS.xg,
      sample: { minMatches: 20 },
    };
    const rows = [row(1, "A", { matches_played: 30 }), row(2, "B", { matches_played: 5 })];
    const { pts, hidden } = plotSet(rows, { x: gated, y: METRICS.xga });
    expect(pts.map((p) => p.name)).toEqual(["A"]);
    expect(hidden).toEqual([{ key: "id:2", name: "B", axis: "x", reason: "sample" }]);
  });

  it("重复行不重复计入 hidden(同一支球队多行只记一次)", () => {
    const rows = [
      row(1, "A", { avg_expected_goals: null }),
      row(1, "A", { avg_expected_goals: null }),
    ];
    const { hidden } = plotSet(rows, view);
    expect(hidden.length).toBe(1);
  });

  it("后面的行有数据时能'救回'前面记为隐藏的球队(锁定既有语义)", () => {
    const rows = [row(1, "A", { avg_expected_goals: null }), row(1, "A")];
    const { pts, hidden } = plotSet(rows, view);
    expect(pts.map((p) => p.name)).toEqual(["A"]);
    expect(hidden).toEqual([]);
  });

  it("collectPoints 就是 plotSet(...).pts,签名对既有调用方保持兼容", () => {
    const rows = [row(1, "A"), row(2, "B", { avg_expected_goals: null })];
    expect(collectPoints(rows, view)).toEqual(plotSet(rows, view).pts);
  });
});

describe("hiddenNote", () => {
  const fullView = { x: METRICS.xg, y: METRICS.xga } as unknown as View;

  it("没有隐藏球队时返回空串——与改造前的摘要逐字一致(e2e 依赖这一点)", () => {
    expect(hiddenNote([], fullView)).toBe("");
  });

  it("只有 missing 时只说数据源缺项,不提门槛", () => {
    const note = hiddenNote(
      [{ key: "id:2", name: "B", axis: "x", reason: "missing" }],
      fullView,
    );
    expect(note).toContain("1 支球队数据源缺这两项之一");
    expect(note).toContain("B");
    expect(note).not.toContain("门槛");
  });

  it("只有 sample 时报门槛与隐藏球队名单", () => {
    const gatedView = {
      x: { ...METRICS.oppHalfPassShare },
      y: METRICS.totalShots,
    } as unknown as View;
    const note = hiddenNote(
      [{ key: "id:3", name: "C", axis: "x", reason: "sample" }],
      gatedView,
    );
    expect(note).toContain("1 支球队样本不足未画出");
    expect(note).toContain("门槛");
    expect(note).toContain("C");
    expect(note).toContain("虚线平均值只统计画出的球队");
  });

  it("missing 与 sample 同时存在时两句话都出现", () => {
    const gatedView = { x: METRICS.oppHalfPassShare, y: METRICS.totalShots } as unknown as View;
    const note = hiddenNote(
      [
        { key: "id:2", name: "B", axis: "x", reason: "missing" },
        { key: "id:3", name: "C", axis: "x", reason: "sample" },
      ],
      gatedView,
    );
    expect(note).toContain("数据源缺这两项之一");
    expect(note).toContain("样本不足未画出");
  });
});
