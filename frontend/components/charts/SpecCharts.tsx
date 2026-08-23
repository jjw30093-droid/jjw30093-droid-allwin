"use client";

/**
 * chart_specs 渲染器:后端 analysis_bundle 的 chart_specs → ECharts。
 * 比赛详情页与 Studio 共用(宪法 §12:不在 Studio 再写一套解释逻辑)。
 *
 * 两个渲染目标共用同一份数据,靠 ChartMode 切换视觉 token:
 * - interactive:网站,12px 字号 + tooltip;
 * - export:Studio 卡片,1080px 宽舞台,30px+ 字号、关动画、数值直接标在图上。
 * 见 components/charts/chartMode.ts。
 */

import type { EChartsOption } from "echarts";
import { EChart } from "@/components/EChart";
import { ShotMapExplorer } from "@/components/charts/ShotMapExplorer";
import { type ChartMode, scaleGrid, tokensFor } from "@/components/charts/chartMode";
import { useChartColors, type ChartColors } from "@/components/charts/useChartColors";
import type { components } from "@/lib/api-types";

/**
 * 图表规格 = OpenAPI 生成的 BundleChartSpec(Pydantic 单一真源)。
 * data 在契约里就是宽 dict({[key]: unknown}),各 type 的具体字段以
 * backend/studio/bundle.py 的 chart_specs 构造为准,本文件按 type 做运行时窄化。
 */
export type ChartSpec = components["schemas"]["BundleChartSpec"];

function probabilityBarOption(
  d: Record<string, unknown>,
  mode: ChartMode,
  c: ChartColors,
): EChartsOption {
  const t = tokensFor(mode);
  const rows = [
    { name: "主胜", value: Number(d.home), color: c.win },
    { name: "平局", value: Number(d.draw), color: c.draw },
    { name: "客胜", value: Number(d.away), color: c.loss },
  ];
  return {
    grid: scaleGrid({ left: 60, right: 40, top: 10, bottom: 10 }, mode),
    xAxis: { type: "value", max: 1, show: false },
    yAxis: {
      type: "category",
      data: rows.map((r) => r.name),
      inverse: true,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: c.ink2, fontSize: t.axisFont },
    },
    series: [
      {
        type: "bar",
        data: rows.map((r) => ({ value: r.value, itemStyle: { color: r.color } })),
        barWidth: t.barWidth,
        label: {
          show: true,
          position: "right",
          color: "#fff",
          fontSize: t.labelFont,
          formatter: ({ value }) => `${Math.round(Number(value) * 100)}%`,
        },
      },
    ],
  };
}

function xgCompareOption(
  d: Record<string, unknown>,
  mode: ChartMode,
  c: ChartColors,
): EChartsOption {
  const t = tokensFor(mode);
  const valueLabel = {
    show: t.alwaysShowValues,
    position: "right" as const,
    color: "#fff",
    fontSize: t.labelFont,
    formatter: ({ value }: { value: unknown }) => Number(value).toFixed(2),
  };
  return {
    grid: scaleGrid({ left: 80, right: 40, top: 30, bottom: 24 }, mode),
    legend: {
      data: ["进攻 xG", "防守失 xG"],
      textStyle: { color: c.ink2, fontSize: t.legendFont },
      top: 0,
      itemHeight: Math.round(t.legendFont * 0.8),
      itemWidth: Math.round(t.legendFont * 1.6),
    },
    xAxis: { type: "value", axisLabel: { color: c.ink2, fontSize: t.axisFont } },
    yAxis: {
      type: "category",
      data: [String(d.home_name), String(d.away_name)],
      axisLabel: { color: c.ink2, fontSize: t.axisFont },
    },
    series: [
      {
        name: "进攻 xG",
        type: "bar",
        data: [Number(d.home_xg_for), Number(d.away_xg_for)],
        color: c.teal,
        barWidth: Math.round(t.barWidth * 0.78),
        label: valueLabel,
      },
      {
        name: "防守失 xG",
        type: "bar",
        data: [Number(d.home_xg_against), Number(d.away_xg_against)],
        color: c.loss,
        barWidth: Math.round(t.barWidth * 0.78),
        label: valueLabel,
      },
    ],
  };
}

export function summarize(spec: ChartSpec): string {
  const d = spec.data;
  if (spec.type === "probability_bar") {
    const label =
      d.probability_source === "MARKET_BASELINE" ? "赔率折算概率" : "模型概率";
    return `${label}:主胜 ${Math.round(Number(d.home) * 100)}%,平局 ${Math.round(Number(d.draw) * 100)}%,客胜 ${Math.round(Number(d.away) * 100)}%`;
  }
  if (spec.type === "xg_compare")
    return `近10场滚动 xG:${d.home_name} 进攻 ${d.home_xg_for} 防守 ${d.home_xg_against};${d.away_name} 进攻 ${d.away_xg_for} 防守 ${d.away_xg_against}`;
  if (spec.type === "shot_map_explorer") {
    const teams = (d.teams as { team_id: number; name: string }[]) ?? [];
    const sets = (d.recent_sets ?? {}) as Record<
      string,
      Record<string, { matched_games: number; shot_covered_games: number }>
    >;
    const parts = teams.map((t) => {
      const s = sets[String(t.team_id)]?.same_league;
      return s ? `${t.name} ${s.shot_covered_games}/${s.matched_games} 场有射门数据` : t.name;
    });
    return `最近 5 场射门分布(同联赛口径):${parts.join(";")}。`;
  }
  return spec.title;
}

export function SpecChart({
  spec,
  height = 220,
  mode = "interactive",
}: {
  spec: ChartSpec;
  height?: number;
  mode?: ChartMode;
}) {
  const c = useChartColors();
  if (spec.type === "shot_map_explorer") {
    // 导出模式必须隐藏筛选控件 —— 截进 PNG 的按钮全是死的。
    return (
      <ShotMapExplorer
        raw={spec.data}
        height={Math.max(height, 360)}
        mode={mode}
      />
    );
  }
  let option: EChartsOption | null = null;
  if (spec.type === "probability_bar") option = probabilityBarOption(spec.data, mode, c);
  else if (spec.type === "xg_compare") option = xgCompareOption(spec.data, mode, c);
  if (!option) return null;
  return <EChart option={option} height={height} ariaSummary={summarize(spec)} mode={mode} />;
}
