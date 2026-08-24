/**
 * 图表渲染冒烟测试(2026-08-24,CLAUDE.md §11.3)。
 *
 * 起因:势头图的 `visualMap.pieces` 开区间配置在项目实际使用的 ECharts
 * ^6.1.0 上会抛 `Cannot read properties of undefined (reading 'coord')`——
 * 这个 bug 在 `vitest run` 424/424 全绿的情况下上线,因为现有图表测试只测
 * `summarizeMomentum`/`buildBuckets`/`filterShots` 这类纯逻辑,从不真的把
 * 构造出的 option 交给 ECharts 渲染一次。
 *
 * 本文件用项目自己的 echarts(而不是新引入一个渲染库)在 Node/jsdom 下做
 * headless SSR 渲染(`renderer:'svg', ssr:true`),对每个图表的
 * `buildOption` 真实调用 `setOption` + `renderToSVGString()`——异常会在这里
 * 被真实抛出而不是被寄望的调用方吞掉。
 */

import * as echarts from "echarts";
import { describe, expect, it } from "vitest";
import { buildOption as buildMomentumOption } from "@/components/matches/MomentumChart";
import { buildBuckets, buildOption as buildThreatOption } from "@/components/matches/ThreatTimeline";
import { cumulativeSeries, buildOption as buildXgRaceOption } from "@/components/matches/XgRaceChart";
import { buildOption as buildShotMapOption } from "@/components/matches/ShotMapChart";
import { buildOption as buildQuadrantOption } from "@/components/matches/TeamStyleQuadrant";
import type { ChartColors } from "@/components/charts/useChartColors";

const COLORS: ChartColors = {
  teal: "#087e78",
  navy: "#1d6f8b",
  win: "#287851",
  loss: "#b83b2d",
  draw: "#706c64",
  ink: "#0d2c3d",
  ink2: "#40535d",
  ink3: "#5a6b73",
  grey: "#b8c6c6",
  surface: "#ffffff",
  isDark: false,
  pitchBg: "#f8fafa",
};

/** 2026-08-24:真实球队配色(FotMob prematch-5104961.json 实测值)代入
 * teal/navy 槽位——证明渲染路径对任意十六进制值都成立,不是只在品牌色上
 * 碰巧不崩。 */
const NON_BRAND_COLORS: ChartColors = { ...COLORS, teal: "#f13c26", navy: "#104070" };

/** 真实渲染一次 option,抛异常即测试失败;返回画出的 <path> 数,便于额外
 * 断言"至少画出点东西"(而不是异常被吞掉后返回一个空壳)。 */
function renderOrThrow(option: echarts.EChartsOption): number {
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 920, height: 200 });
  try {
    chart.setOption(option);
    const svg = chart.renderToSVGString();
    return (svg.match(/<path/g) || []).length;
  } finally {
    chart.dispose();
  }
}

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

describe("MomentumChart.buildOption 渲染冒烟", () => {
  it("真实比赛形状(94 个点,正负穿插)不抛异常且画出线/面", () => {
    const points = Array.from({ length: 94 }, (_, i) => ({ minute: i, value: Math.sin(i / 8) * 40 }));
    const paths = renderOrThrow(buildMomentumOption(points, 94, "interactive", COLORS));
    expect(paths).toBeGreaterThan(0);
  });

  it("空数组不抛异常", () => {
    expect(() => renderOrThrow(buildMomentumOption([], 90, "interactive", COLORS))).not.toThrow();
  });

  it("全场主队占优(无负值)不抛异常", () => {
    const points = Array.from({ length: 10 }, (_, i) => ({ minute: i, value: i + 1 }));
    expect(() => renderOrThrow(buildMomentumOption(points, 90, "interactive", COLORS))).not.toThrow();
  });

  it("全场客队占优(无正值)不抛异常", () => {
    const points = Array.from({ length: 10 }, (_, i) => ({ minute: i, value: -(i + 1) }));
    expect(() => renderOrThrow(buildMomentumOption(points, 90, "interactive", COLORS))).not.toThrow();
  });

  it("export 模式不抛异常", () => {
    const points = Array.from({ length: 20 }, (_, i) => ({ minute: i, value: Math.sin(i) * 10 }));
    expect(() => renderOrThrow(buildMomentumOption(points, 90, "export", COLORS))).not.toThrow();
  });

  it("真实球队配色(非品牌色)不抛异常且画出线/面", () => {
    const points = Array.from({ length: 30 }, (_, i) => ({ minute: i, value: Math.sin(i / 5) * 30 }));
    const paths = renderOrThrow(buildMomentumOption(points, 90, "interactive", NON_BRAND_COLORS));
    expect(paths).toBeGreaterThan(0);
  });
});

describe("ThreatTimeline.buildOption 渲染冒烟", () => {
  it("真实射门数据不抛异常且画出柱子", () => {
    const shots = [
      shot({ minute: 3, xg: 0.2, is_home: true }),
      shot({ minute: 4, xg: 0.5, is_home: true, outcome: "Goal", player_name: "甲" }),
      shot({ minute: 7, xg: 0.3, is_home: false }),
      shot({ minute: 44, xg: 0.6, is_home: false, outcome: "Goal", player_name: "乙" }),
    ];
    const buckets = buildBuckets(shots, 5);
    const paths = renderOrThrow(buildThreatOption(buckets, "主队", "客队", 5, "interactive", COLORS));
    expect(paths).toBeGreaterThan(0);
  });

  it("空射门列表不抛异常", () => {
    const buckets = buildBuckets([], 5);
    expect(() =>
      renderOrThrow(buildThreatOption(buckets, "主队", "客队", 5, "interactive", COLORS)),
    ).not.toThrow();
  });

  it("真实球队配色(非品牌色)不抛异常且画出柱子", () => {
    const shots = [
      shot({ minute: 3, xg: 0.2, is_home: true }),
      shot({ minute: 44, xg: 0.6, is_home: false, outcome: "Goal", player_name: "乙" }),
    ];
    const buckets = buildBuckets(shots, 5);
    const paths = renderOrThrow(
      buildThreatOption(buckets, "主队", "客队", 5, "interactive", NON_BRAND_COLORS),
    );
    expect(paths).toBeGreaterThan(0);
  });
});

describe("XgRaceChart.buildOption 渲染冒烟", () => {
  it("真实射门数据不抛异常且画出曲线", () => {
    const shots = [
      shot({ minute: 3, xg: 0.2, is_home: true }),
      shot({ minute: 20, xg: 0.5, is_home: true, outcome: "Goal" }),
      shot({ minute: 55, xg: 0.3, is_home: false }),
    ];
    const home = cumulativeSeries(shots, true);
    const away = cumulativeSeries(shots, false);
    const paths = renderOrThrow(buildXgRaceOption(home, away, "主队", "客队", 90, "interactive", COLORS));
    expect(paths).toBeGreaterThan(0);
  });

  it("空射门列表不抛异常", () => {
    const home = cumulativeSeries([], true);
    const away = cumulativeSeries([], false);
    expect(() =>
      renderOrThrow(buildXgRaceOption(home, away, "主队", "客队", 90, "interactive", COLORS)),
    ).not.toThrow();
  });

  it("真实球队配色(非品牌色)不抛异常且画出曲线", () => {
    const shots = [
      shot({ minute: 3, xg: 0.2, is_home: true }),
      shot({ minute: 55, xg: 0.3, is_home: false }),
    ];
    const home = cumulativeSeries(shots, true);
    const away = cumulativeSeries(shots, false);
    const paths = renderOrThrow(
      buildXgRaceOption(home, away, "主队", "客队", 90, "interactive", NON_BRAND_COLORS),
    );
    expect(paths).toBeGreaterThan(0);
  });
});

describe("ShotMapChart.buildOption 渲染冒烟", () => {
  const plottedShots = [
    shot({ minute: 5, xg: 0.1, is_home: true, x: 90, y: 34, outcome: "AttemptSaved" }),
    shot({ minute: 20, xg: 0.4, is_home: true, x: 100, y: 36, outcome: "Goal" }),
    shot({ minute: 60, xg: 0.2, is_home: false, x: 95, y: 30, outcome: "Miss" }),
  ];

  it("真实射门数据不抛异常且画出标记点", () => {
    const paths = renderOrThrow(buildShotMapOption(plottedShots, "主队", "客队", COLORS));
    expect(paths).toBeGreaterThan(0);
  });

  it("空射门列表不抛异常", () => {
    expect(() => renderOrThrow(buildShotMapOption([], "主队", "客队", COLORS))).not.toThrow();
  });

  it("真实球队配色(非品牌色)不抛异常且画出标记点", () => {
    const paths = renderOrThrow(buildShotMapOption(plottedShots, "主队", "客队", NON_BRAND_COLORS));
    expect(paths).toBeGreaterThan(0);
  });
});

describe("TeamStyleQuadrant.buildOption 渲染冒烟", () => {
  const view = {
    id: "xg-for-against",
    tab: "攻防",
    title: "攻防风格",
    x_label: "场均 xG",
    y_label: "场均 xGA",
    digits: 2,
    quadrants: ["攻强守强", "攻强守弱", "攻弱守强", "攻弱守弱"],
    points: [],
    y_lower_is_better: true,
  };
  const pts = [
    { team_id: 1, name: "主队", x: 1.8, y: 0.9 },
    { team_id: 2, name: "客队", x: 1.2, y: 1.4 },
    { team_id: 3, name: "第三队", x: 1.5, y: 1.1 },
    { team_id: 4, name: "第四队", x: 0.9, y: 1.6 },
  ];

  it("真实分布数据不抛异常且画出散点", () => {
    const paths = renderOrThrow(buildQuadrantOption(view, pts, 1.35, 1.25, 1, 2, COLORS));
    expect(paths).toBeGreaterThan(0);
  });

  it("真实球队配色(非品牌色)不抛异常且画出散点", () => {
    const paths = renderOrThrow(buildQuadrantOption(view, pts, 1.35, 1.25, 1, 2, NON_BRAND_COLORS));
    expect(paths).toBeGreaterThan(0);
  });
});
