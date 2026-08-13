"use client";

/**
 * 全站唯一图表封装(ECharts;宪法 §3.1 只保留一个图表库)。
 * 必须传 ariaSummary:图表的文字摘要(宪法 §11.2 图表要有文字摘要与空态)。
 *
 * mode="export" 用于 Studio 卡片(1080px 宽导出舞台):关动画、文字摘要放大到
 * 卡片可读字号。视觉 token 见 components/charts/chartMode.ts。
 */

import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { EChartsOption } from "echarts";
import type { ChartMode } from "@/components/charts/chartMode";

export function EChart({
  option,
  height = 280,
  ariaSummary,
  className,
  mode = "interactive",
  showSummary = true,
}: {
  option: EChartsOption;
  height?: number;
  ariaSummary: string;
  className?: string;
  mode?: ChartMode;
  /** 卡片自带说明文字时可关掉内置摘要,避免重复(a11y label 仍保留在容器上)。 */
  showSummary?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chartRef.current = chart;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    // 导出模式关动画:html-to-image 截图时动画未完成会截到半截图形。
    const resolved: EChartsOption =
      mode === "export" ? { ...option, animation: false } : option;
    chartRef.current?.setOption(resolved, { notMerge: true });
  }, [option, mode]);

  return (
    <div className={className}>
      <div ref={ref} style={{ width: "100%", height }} role="img" aria-label={ariaSummary} />
      {showSummary && (
        <p className="chart-summary" data-chart-mode={mode}>
          {ariaSummary}
        </p>
      )}
    </div>
  );
}
