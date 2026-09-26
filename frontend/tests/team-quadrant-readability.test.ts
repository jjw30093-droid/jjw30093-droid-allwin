/**
 * 象限图"看得清"改造(2026-09-26):8% 余量的坐标范围、整刻度、临界带、
 * 象限底色对比度、标注碰撞检测、enhance 渲染冒烟。
 */

import * as echarts from "echarts";
import { describe, expect, it } from "vitest";
import { niceAxisRange, firstClearRect, rectHitsCircle, rectsOverlap } from "@/components/charts/crestQuadrantLayout";
import { contrastRatio, hexToRgb, type Rgb } from "@/components/charts/colorContrast";
import { LABEL_INK_MIX, buildQuadrantOption, mixToward, niceTickValues } from "@/components/league/quadrantOption";
import { meanBandHalf, nearMeanTeams, viewById, type Pt } from "@/components/league/quadrantViews";
import type { ChartColors } from "@/components/charts/useChartColors";

const composite = (fg: Rgb, bg: Rgb, a: number): Rgb =>
  fg.map((c, i) => Math.round(c * a + bg[i] * (1 - a))) as Rgb;

describe("niceAxisRange snap:false(两端只留 8% 余量)", () => {
  it("端点 = 数据范围外扩 8%,不吸附到刻度", () => {
    const r = niceAxisRange([1, 2, 3], { pad: 0.08, snap: false });
    expect(r.min).toBeCloseTo(0.84, 6);
    expect(r.max).toBeCloseTo(3.16, 6);
    expect(r.interval).toBeGreaterThan(0);
  });

  it("数据全 ≥ 0 时轴不跌破 0(球员象限图 2026-09-16 那条规则在 snap:false 下依然成立)", () => {
    const r = niceAxisRange([0.02, 0.5, 1], { pad: 0.08, snap: false });
    expect(r.min).toBeGreaterThanOrEqual(0);
  });

  it("不传 snap 时与改造前逐字节一致(默认吸附到 nice 端点)", () => {
    const r = niceAxisRange([0.9, 1.4, 2.3]);
    expect(r).toEqual({ min: 0.5, max: 2.5, interval: 0.5 });
  });

  it("niceTickValues:只给 interval 整数倍的刻度,且都落在 [min,max] 内", () => {
    const r = niceAxisRange([1.13, 1.9, 2.71], { pad: 0.08, snap: false });
    const ticks = niceTickValues(r);
    expect(ticks.length).toBeGreaterThan(0);
    for (const t of ticks) {
      expect(t).toBeGreaterThanOrEqual(r.min - 1e-9);
      expect(t).toBeLessThanOrEqual(r.max + 1e-9);
      const k = t / r.interval;
      expect(Math.abs(k - Math.round(k))).toBeLessThan(1e-6);
    }
  });
});

describe("均值临界带", () => {
  it("正值轴:半宽 = 均值的 5%", () => {
    expect(meanBandHalf(2, [1, 2, 3])).toBeCloseTo(0.1, 9);
  });

  it("轴上有负值(终结超额/扑救超额)时改用数据跨度的 5%,不因均值贴近 0 而退化成没有带", () => {
    expect(meanBandHalf(0.01, [-0.5, 0.01, 0.5])).toBeCloseTo(0.05, 9);
  });

  it("nearMeanTeams:任一轴落在带内(含边界)即为临界", () => {
    const pts = [
      { key: "a", x: 1.5, y: 1.0 }, // x 恰好在均值上
      { key: "b", x: 1.58, y: 2.0 }, // x 差 0.08 > 带宽 0.075;y 远
      { key: "c", x: 3, y: 1.52 }, // y 在带内
      { key: "d", x: 3, y: 2 },
    ];
    const near = nearMeanTeams(pts, 1.5, 1.5, 0.075, 0.075);
    expect([...near].sort()).toEqual(["a", "c"]);
  });
});

describe("标注碰撞检测", () => {
  const rect = { left: 10, top: 10, right: 60, bottom: 24 };

  it("rectHitsCircle:圆心在矩形外但半径够到 → 相交;够不到 → 不相交", () => {
    expect(rectHitsCircle(rect, { cx: 66, cy: 17, radius: 8 })).toBe(true);
    expect(rectHitsCircle(rect, { cx: 80, cy: 17, radius: 8 })).toBe(false);
  });

  it("rectsOverlap", () => {
    expect(rectsOverlap(rect, { left: 55, top: 20, right: 90, bottom: 40 })).toBe(true);
    expect(rectsOverlap(rect, { left: 61, top: 0, right: 90, bottom: 9 })).toBe(false);
  });

  it("firstClearRect:选第一个完全干净的候选", () => {
    const cands = [rect, { left: 100, top: 100, right: 150, bottom: 114 }];
    expect(firstClearRect(cands, [{ cx: 30, cy: 17, radius: 10 }])).toBe(1);
  });

  it("没有完全干净的候选时,选压得最少的(不是无脑退回 0)", () => {
    const a = { left: 0, top: 0, right: 50, bottom: 14 };
    const b = { left: 200, top: 0, right: 250, bottom: 14 };
    const circles = [
      { cx: 10, cy: 7, radius: 6 },
      { cx: 30, cy: 7, radius: 6 }, // 压住 a 两个
      { cx: 220, cy: 7, radius: 6 }, // 只压住 b 一个
    ];
    expect(firstClearRect([a, b], circles)).toBe(1);
  });

  it("压已放好的其它标签,比压队徽罚得更重", () => {
    const a = { left: 0, top: 0, right: 50, bottom: 14 };
    const b = { left: 200, top: 0, right: 250, bottom: 14 };
    const circles = [{ cx: 220, cy: 7, radius: 6 }, { cx: 240, cy: 7, radius: 6 }]; // b 压 2 个队徽 = 2 分
    expect(firstClearRect([a, b], circles, [{ left: 20, top: 0, right: 40, bottom: 14 }])).toBe(1); // a 压 1 个标签 = 10 分
  });
});

// 与 frontend/app/globals.css 保持同步(与 team-quadrant-contrast.test.ts 同一做法)
const THEMES = [
  {
    label: "浅色模式",
    surface: hexToRgb("#ffffff"),
    ink: hexToRgb("#0d2c3d"),
    win: hexToRgb("#287851"),
    loss: hexToRgb("#b83b2d"),
    teal: hexToRgb("#087e78"),
  },
  {
    label: "深色模式",
    surface: hexToRgb("#0d2029"),
    ink: hexToRgb("#eef5f4"),
    win: hexToRgb("#68c994"),
    loss: hexToRgb("#ef7865"),
    teal: hexToRgb("#45b9af"),
  },
];

describe("回归护栏", () => {
  it("浅色主题:不混合的 --win 原色压在 0.08 绿底 + 临界带上只有约 4.4:1,低于 4.5——所以象限名必须朝 ink 混合", () => {
    const { surface, ink, win } = THEMES[0];
    const worst = composite(ink, composite(win, surface, 0.08), 0.05);
    expect(contrastRatio(win, worst)).toBeLessThan(4.5);
  });
});

describe.each(THEMES)("象限底色对比度($label)", ({ surface, ink, win, loss, teal }) => {
  // 最坏底色:好象限的 0.08 绿/红底 再叠一层 0.05 的均值临界带
  const winBg = composite(ink, composite(win, surface, 0.08), 0.05);
  const lossBg = composite(ink, composite(loss, surface, 0.08), 0.05);
  const plain = composite(ink, surface, 0.05);

  it("象限名(粗体 15px,需 ≥ 4.5:1)压在对应底色上——用朝 ink 混合后的实际文字色", () => {
    const toHex = (c: Rgb) => "#" + c.map((n) => n.toString(16).padStart(2, "0")).join("");
    const label = (c: Rgb) => hexToRgb(mixToward(toHex(c), toHex(ink), LABEL_INK_MIX));
    expect(contrastRatio(label(win), winBg)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(label(loss), lossBg)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(label(teal), plain)).toBeGreaterThanOrEqual(4.5);
  });

  it("无队徽兜底圆点(全不透明)压在任一底色上 ≥ 3:1", () => {
    for (const dot of [win, loss, teal]) {
      for (const bg of [winBg, lossBg, plain, surface]) {
        expect(contrastRatio(dot, bg)).toBeGreaterThanOrEqual(3);
      }
    }
  });

  it("均值线标注(--ink)压在半透明卡片底上 ≥ 4.5:1", () => {
    const label = composite(surface, plain, 0.85);
    expect(contrastRatio(ink, label)).toBeGreaterThanOrEqual(4.5);
  });
});

// ---------- enhance 渲染冒烟 ----------
const COLORS: ChartColors = {
  teal: "#087e78", navy: "#1d6f8b", win: "#287851", loss: "#b83b2d", draw: "#706c64",
  ink: "#0d2c3d", ink2: "#40535d", ink3: "#5a6b73", grey: "#b8c6c6", surface: "#ffffff",
  isDark: false, pitchBg: "#f8fafa",
};

function pts(n: number): Pt[] {
  return Array.from({ length: n }, (_, i) => ({
    key: `t${i}`, name: `球队${i}`, x: 1 + (i % 5) * 0.4, y: 0.9 + ((i * 3) % 7) * 0.2, mp: 5, crestUrl: null, teamId: i,
  }));
}

function svgOf(
  viewId: string,
  box: { width: number; height: number } | null,
  extra: { labelled?: Set<string>; badges?: Record<string, string> } = {},
) {
  const view = viewById(viewId);
  const p = pts(12);
  const grid = { left: 46, right: 26, top: 58, bottom: 66 };
  const xr = niceAxisRange(p.map((q) => q.x), { pad: 0.08, snap: false });
  const yr = niceAxisRange(p.map((q) => q.y), { pad: 0.08, snap: false });
  const layout = box
    ? {
        base: p.map((q) => ({
          px: grid.left + ((q.x - xr.min) / (xr.max - xr.min)) * (box.width - grid.left - grid.right),
          py: grid.top + ((q.y - yr.min) / (yr.max - yr.min)) * (box.height - grid.top - grid.bottom),
        })),
        offset: p.map(() => [0, 0] as [number, number]),
        leader: p.map(() => false),
        minSpacing: 40,
      }
    : null;
  const option = buildQuadrantOption({
    view, pts: p, mx: 1.8, my: 1.5, colors: COLORS, labelled: extra.labelled ?? new Set(), badges: extra.badges, crestSize: 28, layout, xr, yr, grid,
    selectedIndexes: [], axisHints: true,
    enhance: { meanLabel: "联赛平均", bandX: 0.09, bandY: 0.075, box },
  });
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: box?.width ?? 360, height: box?.height ?? 520 });
  try {
    chart.setOption(option);
    return chart.renderToSVGString();
  } finally {
    chart.dispose();
  }
}

describe("enhance 渲染冒烟", () => {
  it("测得容器尺寸:象限名 4 个 + 两条均值线标注真的画出来", () => {
    const svg = svgOf("both-ends", { width: 360, height: 520 });
    for (const t of ["两头都强", "守强攻弱", "两头都弱", "对攻型"]) expect(svg).toContain(t);
    expect(svg).toContain("联赛平均 1.80"); // x 均值
    expect(svg).toContain("联赛平均 1.50"); // y 均值
  });

  it("首帧(尚未测得容器尺寸):退回旧的数据坐标象限名,不抛异常、不画均值标注", () => {
    const svg = svgOf("both-ends", null);
    expect(svg).toContain("两头都强");
    expect(svg).not.toContain("联赛平均 1.80");
  });

  it("自动标注的序号前缀真的出现在图上的队名标签里(标签在图上、序号与图下列表对应)", () => {
    const svg = svgOf("both-ends", { width: 360, height: 520 }, { labelled: new Set(["球队0"]), badges: { 球队0: "①" } });
    expect(svg).toContain("①球队0");
  });

  it("风格视角(两轴非 performance)也能渲染", () => {
    expect(() => svgOf("set-piece-both-ends", { width: 360, height: 520 })).not.toThrow();
  });
});
