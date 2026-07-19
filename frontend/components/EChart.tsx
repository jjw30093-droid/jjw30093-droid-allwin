"use client";

/**
 * 全站唯一图表封装(ECharts;宪法 §3.1 只保留一个图表库)。
 * 必须传 ariaSummary:图表的文字摘要(宪法 §11.2 图表要有文字摘要与空态)。
 */

import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { EChartsOption } from "echarts";

export function EChart({
  option,
  height = 280,
  ariaSummary,
  className,
}: {
  option: EChartsOption;
  height?: number;
  ariaSummary: string;
  className?: string;
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
    chartRef.current?.setOption(option, { notMerge: true });
  }, [option]);

  return (
    <div className={className}>
      <div ref={ref} style={{ width: "100%", height }} role="img" aria-label={ariaSummary} />
      <p className="chart-summary">{ariaSummary}</p>
    </div>
  );
}
