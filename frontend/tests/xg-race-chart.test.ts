import * as echarts from "echarts";
import { describe, expect, it } from "vitest";
import type { ChartColors } from "@/components/charts/useChartColors";
import { contrastRatioHex } from "@/components/charts/colorContrast";
import { MATCH_FALLBACK_COLORS } from "@/components/charts/matchTeamColors";
import {
  buildOption,
  cumulativeSeries,
  stoppageAxisLabel,
  stoppageAxisPosition,
  stoppageTicks,
  type XgRaceShot,
} from "@/components/matches/XgRaceChart";

const COLORS: ChartColors = {
  teal: "#087e78",
  navy: "#b45309",
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

function shot(p: Partial<XgRaceShot>): XgRaceShot {
  return {
    player_id: "p",
    player_name: "球员",
    team_id: 1,
    is_home: true,
    minute: 10,
    period: "FirstHalf",
    xg: 0.1,
    outcome: "Miss",
    is_own_goal: false,
    is_own_goal_inferred: false,
    ...p,
  };
}

type MarkPoint = { data: { itemStyle: { color: string } }[]; label: { color: string } };

function render(option: echarts.EChartsOption): string {
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 920, height: 260 });
  try {
    chart.setOption(option);
    return chart.renderToSVGString();
  } finally {
    chart.dispose();
  }
}

describe("XgRaceChart 进球配色", () => {
  it("主队进球用主队色,客队进球用客队色(点与标签)", () => {
    const shots = [
      shot({ minute: 20, is_home: true, outcome: "Goal", player_name: "主队射手" }),
      shot({ minute: 31, is_home: false, outcome: "Goal", player_name: "格埃希" }),
    ];
    const opt = buildOption(cumulativeSeries(shots, true), cumulativeSeries(shots, false), "主", "客", 90, "interactive", COLORS);
    const [home, away] = opt.series as { markPoint: MarkPoint }[];
    expect(home.markPoint.data[0].itemStyle.color).toBe(COLORS.teal);
    expect(home.markPoint.label.color).toBe(COLORS.teal);
    expect(away.markPoint.data[0].itemStyle.color).toBe(COLORS.navy);
    expect(away.markPoint.label.color).toBe(COLORS.navy);
    expect(away.markPoint.data[0].itemStyle.color).not.toBe(COLORS.teal);
  });

  it("兜底主客色作为进球标签文字,对卡片底对比度 ≥ 4.5:1(深浅两套)", () => {
    for (const [palette, bg] of [[MATCH_FALLBACK_COLORS.light, "#ffffff"], [MATCH_FALLBACK_COLORS.dark, "#0d2029"]] as const) {
      expect(contrastRatioHex(palette.home, bg)).toBeGreaterThanOrEqual(4.5);
      expect(contrastRatioHex(palette.away, bg)).toBeGreaterThanOrEqual(4.5);
    }
  });
});

describe("XgRaceChart 补时横轴(可选参数)", () => {
  const st = { firstHalf: 3, secondHalf: 7 };

  it("上半场补时排在 45' 之后、下半场之前;下半场右移上半场补时", () => {
    expect(stoppageAxisPosition({ minute: 45, period: "FirstHalf", minute_added: 0 }, st)).toBe(45);
    expect(stoppageAxisPosition({ minute: 45, period: "FirstHalf", minute_added: 2 }, st)).toBe(47);
    expect(stoppageAxisPosition({ minute: 46, period: "SecondHalf", minute_added: 0 }, st)).toBe(49);
    expect(stoppageAxisPosition({ minute: 90, period: "SecondHalf", minute_added: 5 }, st)).toBe(98);
    expect(stoppageAxisLabel(47, st)).toBe("45+2'");
    expect(stoppageAxisLabel(49, st)).toBe("46'");
    expect(stoppageAxisLabel(93, st)).toBe("90'");
    expect(stoppageAxisLabel(98, st)).toBe("90+5'");
    expect(stoppageTicks(st)).toEqual([0, 15, 30, 45, 63, 78, 93, 100]);
  });

  it("点序列按展开后的位置排序,补时进球落在补时区间", () => {
    const shots = [
      shot({ minute: 46, period: "SecondHalf", xg: 0.2 }),
      shot({ minute: 45, period: "FirstHalf", minute_added: 2, xg: 0.3, outcome: "Goal" }),
    ];
    const pts = cumulativeSeries(shots, true, st);
    expect(pts.map((p) => p.minute)).toEqual([0, 47, 49]);
    expect(pts[1].goal?.minute_added).toBe(2);
  });

  it("不传参数时行为不变:横轴按原始分钟、刻度间隔 15、终点为传入的 endMinute", () => {
    const shots = [shot({ minute: 45, minute_added: 2, xg: 0.3 }), shot({ minute: 50, period: "SecondHalf", xg: 0.1 })];
    const pts = cumulativeSeries(shots, true);
    expect(pts.map((p) => p.minute)).toEqual([0, 45, 50]);
    const opt = buildOption(pts, cumulativeSeries(shots, false), "主", "客", 90, "interactive", COLORS);
    const x = opt.xAxis as { max: number; interval?: number };
    expect(x.max).toBe(90);
    expect(x.interval).toBe(15);
    const series = opt.series as { markArea?: unknown }[];
    expect(series[0].markArea).toBeUndefined();
  });

  it("启用后真实渲染不抛异常,画出补时标签与补时底色", () => {
    const shots = [
      shot({ minute: 45, minute_added: 2, xg: 0.3, outcome: "Goal" }),
      shot({ minute: 90, period: "SecondHalf", minute_added: 6, xg: 0.4, is_home: false, outcome: "Goal" }),
    ];
    const end = 90 + st.firstHalf + st.secondHalf;
    const opt = buildOption(cumulativeSeries(shots, true, st), cumulativeSeries(shots, false, st), "主", "客", end, "interactive", COLORS, st);
    const x = opt.xAxis as { max: number; interval?: number };
    expect(x.max).toBe(100);
    expect(x.interval).toBeUndefined();
    const svg = render(opt).replace(/&#39;/g, "'");
    const labels = (svg.match(/<text[^>]*>[^<]*<\/text>/g) || []).map((t) => t.replace(/<[^>]+>/g, ""));
    expect(labels).toEqual(expect.arrayContaining(["0'", "45'", "60'", "90'", "90+7'"]));
    expect((svg.match(/<(path|rect)\b/g) || []).length).toBeGreaterThan(0);
  });
});
