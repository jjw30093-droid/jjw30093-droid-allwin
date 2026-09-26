/**
 * 势头图(2026-09-26):中间基线、主上客下、两色都清楚可见、y 轴无刻度。
 * 纯 option 断言 + 一次真实 ECharts 渲染(§11.3:不能只测纯函数,渲染异常必须真实抛出)。
 */
import * as echarts from "echarts";
import { describe, expect, it } from "vitest";
import { buildOption } from "@/components/matches/MomentumChart";
import { MATCH_FALLBACK_COLORS as MOMENTUM_FALLBACK_COLORS } from "@/components/charts/matchTeamColors";
import {
  colorsDistinct,
  contrastRatioHex,
  hexToRgb,
  MIN_CONTRAST,
} from "@/components/charts/colorContrast";
import type { ChartColors } from "@/components/charts/useChartColors";

const COLORS: ChartColors = {
  teal: "#f13c26",
  navy: "#104070",
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

const POINTS = [
  { minute: 0, value: 0 },
  { minute: 10, value: 80 }, // 主队大幅占优
  { minute: 20, value: -12 }, // 客队小幅占优
  { minute: 30, value: 25 },
];

type AnyOpt = {
  yAxis: { show: boolean; min: number; max: number };
  xAxis: { axisLine: { show: boolean } };
  series: Array<{
    lineStyle: { width: number };
    areaStyle: { opacity: number };
    markLine: { data: Array<{ yAxis: number }>; lineStyle: { width: number; opacity: number; type: string } };
  }>;
};

describe("势头图 buildOption:基线与对称", () => {
  const opt = buildOption(POINTS, 90, "interactive", COLORS) as unknown as AnyOpt;

  it("y 轴无刻度(整条轴隐藏)", () => {
    expect(opt.yAxis.show).toBe(false);
  });

  it("y 轴上下严格对称 → 基线 y=0 正好在图的正中间,即使一边数据远大于另一边", () => {
    expect(opt.yAxis.min).toBe(-opt.yAxis.max);
    expect(opt.yAxis.max).toBeGreaterThanOrEqual(80);
  });

  it("画出 y=0 的中间基线:实线、不透明度 ≥0.8、线宽 ≥1.5", () => {
    const ml = opt.series[0].markLine;
    expect(ml.data).toEqual([{ yAxis: 0 }]);
    expect(ml.lineStyle.type).toBe("solid");
    expect(ml.lineStyle.opacity).toBeGreaterThanOrEqual(0.8);
    expect(ml.lineStyle.width).toBeGreaterThanOrEqual(1.5);
  });

  it("x 轴自带的线与刻度关闭,避免与基线叠成双线", () => {
    expect(opt.xAxis.axisLine.show).toBe(false);
  });

  it("填色不透明度 ≥0.5,曲线线宽 ≥2(客队深色不再发灰)", () => {
    expect(opt.series[0].areaStyle.opacity).toBeGreaterThanOrEqual(0.5);
    expect(opt.series[0].lineStyle.width).toBeGreaterThanOrEqual(2);
  });

  it("真实渲染:不抛异常,且 SVG 里画出了主队色与客队色", () => {
    const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 360, height: 180 });
    chart.setOption(buildOption(POINTS, 90, "interactive", COLORS));
    const svg = chart.renderToSVGString();
    chart.dispose();
    expect(svg.toLowerCase()).toContain("#f13c26");
    expect(svg.toLowerCase()).toContain("#104070");
  });
});

describe("势头图配色可见性(合成对比度,§11.3)", () => {
  /** 把前景以 alpha 叠在背景上,得到实际落在屏幕上的填色 */
  function composite(fg: string, bg: string, alpha: number): string {
    const f = hexToRgb(fg);
    const b = hexToRgb(bg);
    const c = f.map((v, i) => Math.round(v * alpha + b[i] * (1 - alpha)));
    return `#${c.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
  }

  it("曲线(主/客)对卡片底色 ≥3:1", () => {
    expect(contrastRatioHex(COLORS.teal, COLORS.surface)).toBeGreaterThanOrEqual(MIN_CONTRAST);
    expect(contrastRatioHex(COLORS.navy, COLORS.surface)).toBeGreaterThanOrEqual(MIN_CONTRAST);
  });

  it("填色叠在卡片底色上后,与底色仍有可分辨的差距(两边都 ≥1.3:1)", () => {
    for (const color of [COLORS.teal, COLORS.navy]) {
      const filled = composite(color, COLORS.surface, 0.5);
      expect(contrastRatioHex(filled, COLORS.surface)).toBeGreaterThanOrEqual(1.3);
    }
  });

  it("基线颜色(ink2)对卡片底色 ≥3:1", () => {
    expect(contrastRatioHex(COLORS.ink2, COLORS.surface)).toBeGreaterThanOrEqual(MIN_CONTRAST);
  });
});

describe("colorsDistinct:主客两色一眼能分开", () => {
  it("红 vs 深蓝:可分", () => expect(colorsDistinct("#f13c26", "#104070")).toBe(true));
  it("两个相近的蓝:不可分", () => expect(colorsDistinct("#1d6f8b", "#104070")).toBe(false));
  it("相同颜色:不可分", () => expect(colorsDistinct("#087e78", "#087e78")).toBe(false));
  it("全站品牌青绿 vs 品牌蓝并不可分(RGB 距离≈32)——这正是势头图不用它做兜底的原因", () =>
    expect(colorsDistinct("#087e78", "#1d6f8b")).toBe(false));
  it("非法十六进制:保守返回 false", () => expect(colorsDistinct("red", "#104070")).toBe(false));
});

describe("势头图兜底配色(真实球队色缺失时)", () => {
  const SURFACE = { light: "#ffffff", dark: "#0d2029" } as const;
  for (const theme of ["light", "dark"] as const) {
    const pair = MOMENTUM_FALLBACK_COLORS[theme];
    it(`${theme}:主客两色可分`, () => expect(colorsDistinct(pair.home, pair.away)).toBe(true));
    it(`${theme}:两色对卡片底色 ≥3:1`, () => {
      expect(contrastRatioHex(pair.home, SURFACE[theme])).toBeGreaterThanOrEqual(MIN_CONTRAST);
      expect(contrastRatioHex(pair.away, SURFACE[theme])).toBeGreaterThanOrEqual(MIN_CONTRAST);
    });
  }
});
