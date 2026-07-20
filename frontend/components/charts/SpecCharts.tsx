"use client";

/**
 * chart_specs 渲染器:后端 analysis_bundle 的 chart_specs → ECharts。
 * 比赛详情页与 Studio 共用(宪法 §12:不在 Studio 再写一套解释逻辑)。
 */

import type { EChartsOption } from "echarts";
import { EChart } from "@/components/EChart";
import type { components } from "@/lib/api-types";

/**
 * 图表规格 = OpenAPI 生成的 BundleChartSpec(Pydantic 单一真源)。
 * data 在契约里就是宽 dict({[key]: unknown}),各 type 的具体字段以
 * backend/studio/bundle.py 的 chart_specs 构造为准,本文件按 type 做运行时窄化。
 */
export type ChartSpec = components["schemas"]["BundleChartSpec"];

const GOLD = "#d49e33";
const INK2 = "#a79c87";
const WIN = "#4e9a5b";
const LOSS = "#c05437";
const DRAW = "#8a8069";

function probabilityBarOption(d: Record<string, unknown>): EChartsOption {
  const rows = [
    { name: "主胜", value: Number(d.home), color: WIN },
    { name: "平局", value: Number(d.draw), color: DRAW },
    { name: "客胜", value: Number(d.away), color: LOSS },
  ];
  return {
    grid: { left: 60, right: 40, top: 10, bottom: 10 },
    xAxis: { type: "value", max: 1, show: false },
    yAxis: {
      type: "category",
      data: rows.map((r) => r.name),
      inverse: true,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: INK2 },
    },
    series: [
      {
        type: "bar",
        data: rows.map((r) => ({ value: r.value, itemStyle: { color: r.color } })),
        barWidth: 18,
        label: {
          show: true,
          position: "right",
          color: "#f3ecdc",
          formatter: ({ value }) => `${Math.round(Number(value) * 100)}%`,
        },
      },
    ],
  };
}

function formCompareOption(d: Record<string, unknown>): EChartsOption {
  const score = (r: string) => (r === "W" ? 3 : r === "D" ? 1 : 0);
  const home = (d.home as string[]) ?? [];
  const away = (d.away as string[]) ?? [];
  const n = Math.max(home.length, away.length);
  return {
    grid: { left: 40, right: 16, top: 30, bottom: 24 },
    legend: {
      data: [String(d.home_name), String(d.away_name)],
      textStyle: { color: INK2 },
      top: 0,
    },
    xAxis: {
      type: "category",
      data: Array.from({ length: n }, (_, i) => `近${n - i}场`),
      axisLabel: { color: INK2 },
    },
    yAxis: { type: "value", max: 3, axisLabel: { show: false }, splitLine: { show: false } },
    series: [
      { name: String(d.home_name), type: "line", data: [...home].reverse().map(score), color: GOLD },
      { name: String(d.away_name), type: "line", data: [...away].reverse().map(score), color: INK2 },
    ],
  };
}

function xgCompareOption(d: Record<string, unknown>): EChartsOption {
  return {
    grid: { left: 80, right: 40, top: 30, bottom: 24 },
    legend: { data: ["进攻 xG", "防守失 xG"], textStyle: { color: INK2 }, top: 0 },
    xAxis: { type: "value", axisLabel: { color: INK2 } },
    yAxis: {
      type: "category",
      data: [String(d.home_name), String(d.away_name)],
      axisLabel: { color: INK2 },
    },
    series: [
      {
        name: "进攻 xG",
        type: "bar",
        data: [Number(d.home_xg_for), Number(d.away_xg_for)],
        color: GOLD,
        barWidth: 14,
      },
      {
        name: "防守失 xG",
        type: "bar",
        data: [Number(d.home_xg_against), Number(d.away_xg_against)],
        color: LOSS,
        barWidth: 14,
      },
    ],
  };
}

export function summarize(spec: ChartSpec): string {
  const d = spec.data;
  if (spec.type === "probability_bar")
    return `模型概率:主胜 ${Math.round(Number(d.home) * 100)}%,平局 ${Math.round(Number(d.draw) * 100)}%,客胜 ${Math.round(Number(d.away) * 100)}%`;
  if (spec.type === "form_compare")
    return `近期战绩:${d.home_name} ${(d.home as string[]).join("")} / ${d.away_name} ${(d.away as string[]).join("")}`;
  if (spec.type === "xg_compare")
    return `近10场滚动 xG:${d.home_name} 进攻 ${d.home_xg_for} 防守 ${d.home_xg_against};${d.away_name} 进攻 ${d.away_xg_for} 防守 ${d.away_xg_against}`;
  return spec.title;
}

export function SpecChart({ spec, height = 220 }: { spec: ChartSpec; height?: number }) {
  let option: EChartsOption | null = null;
  if (spec.type === "probability_bar") option = probabilityBarOption(spec.data);
  else if (spec.type === "form_compare") option = formCompareOption(spec.data);
  else if (spec.type === "xg_compare") option = xgCompareOption(spec.data);
  if (!option) return null;
  return <EChart option={option} height={height} ariaSummary={summarize(spec)} />;
}
