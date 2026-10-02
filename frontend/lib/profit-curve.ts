/**
 * 每日精选盈利走势(2026-10-01 站长要求:"做个盈利图……比如 10000 本金一场 500,能盈利多少")。
 *
 * 数据是后端 /api/v1/reco/track-record 的 curve:全部已结算精选按时间正序的累计盈亏(单位),
 * 与上方战绩摘要的「盈利(单位)」同一口径,最后一点恒等于它。这里只做两件事:
 * - 把单位换成钱:每单投 STAKE 元(1 单位 = STAKE 元),本金 BANKROLL 元;
 * - 构造 ECharts option(纯函数,§11.3:可被 headless 渲染冒烟测试真实调用)。
 *
 * 不带 "use client"。
 */
import type { EChartsOption } from "echarts";
import type { GetJson } from "@/lib/api-v1";
import type { ChartColors } from "@/components/charts/useChartColors";

export type CurvePoint = GetJson<"/api/v1/reco/track-record">["curve"][number];

export const STAKE_YUAN = 500;
export const BANKROLL_YUAN = 10000;

export type ProfitScenario = {
  count: number;
  from: string;
  to: string;
  /** 累计盈亏(元) */
  totalYuan: number;
  endBankrollYuan: number;
  /** 相对本金的涨跌(%) */
  pct: number;
  /** 从之前的最高点回落最多多少元(最大回撤,≥0) */
  maxDrawdownYuan: number;
};

export function toYuan(units: number, stake = STAKE_YUAN): number {
  return Math.round(units * stake);
}

export function profitScenario(
  points: ReadonlyArray<CurvePoint>,
  stake = STAKE_YUAN,
  bankroll = BANKROLL_YUAN,
): ProfitScenario | null {
  if (points.length === 0) return null;
  let peak = 0;
  let maxDd = 0;
  for (const p of points) {
    const cum = p.cum_units * stake;
    peak = Math.max(peak, cum);
    maxDd = Math.max(maxDd, peak - cum);
  }
  const totalYuan = toYuan(points[points.length - 1].cum_units, stake);
  return {
    count: points.length,
    from: points[0].slip_date,
    to: points[points.length - 1].slip_date,
    totalYuan,
    endBankrollYuan: bankroll + totalYuan,
    pct: Math.round((totalYuan / bankroll) * 1000) / 10,
    maxDrawdownYuan: Math.round(maxDd),
  };
}

/** 千分位:13835 → "13,835"(不用 toLocaleString,避免依赖浏览器地区设置) */
export function thousands(v: number): string {
  const n = Math.round(Math.abs(v));
  const str = String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return v < 0 ? `-${str}` : str;
}

/** "+3,835 元" / "-1,000 元" */
export function fmtYuan(v: number, signed = true): string {
  const s = thousands(Math.abs(v));
  if (!signed) return `${v < 0 ? "-" : ""}${s} 元`;
  return `${v > 0 ? "+" : v < 0 ? "-" : ""}${s} 元`;
}

export function buildProfitCurveOption(
  points: ReadonlyArray<CurvePoint>,
  colors: ChartColors,
  stake = STAKE_YUAN,
): EChartsOption {
  // 起点 0(还没出单时的本金),再接每一单之后的累计
  const values = [0, ...points.map((p) => toYuan(p.cum_units, stake))];
  const labels = ["开始", ...points.map((_, i) => `第${i + 1}单`)];
  return {
    animation: false,
    grid: { left: 8, right: 12, top: 28, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "axis",
      formatter: (params: unknown) => {
        const p = (Array.isArray(params) ? params[0] : params) as { dataIndex: number };
        const i = p.dataIndex;
        if (i === 0) return "开始:本金未动";
        const pt = points[i - 1];
        return `${labels[i]} · ${pt.slip_date}<br/>这单 ${fmtYuan(toYuan(pt.net_units, stake))}<br/>累计 ${fmtYuan(values[i])}`;
      },
    },
    xAxis: {
      type: "category",
      data: labels,
      boundaryGap: false,
      axisLabel: { color: colors.ink3, fontSize: 11, interval: Math.max(0, Math.ceil(labels.length / 6) - 1) },
      axisLine: { lineStyle: { color: colors.grey } },
      axisTick: { show: false },
    },
    yAxis: {
      type: "value",
      name: "元",
      nameTextStyle: { color: colors.ink3, fontSize: 11 },
      axisLabel: { color: colors.ink3, fontSize: 11 },
      splitLine: { lineStyle: { color: colors.grey, opacity: 0.5 } },
    },
    series: [
      {
        type: "line",
        data: values,
        showSymbol: false,
        lineStyle: { width: 2.5, color: colors.teal },
        areaStyle: { color: colors.teal, opacity: 0.12 },
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { color: colors.ink3, type: "dashed", width: 1 },
          label: { show: false },
          data: [{ yAxis: 0 }],
        },
      },
    ],
  };
}
