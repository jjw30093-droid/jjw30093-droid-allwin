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
  // "最新值"引用:MatchTabs 用 hidden 属性(而非条件卸载)保留非激活 tab 的
  // 内容,图表容器挂载时可能是 0×0(比如比赛详情页从未打开过的 tab)。init
  // 会被推迟到容器真正拿到非零尺寸的那一刻,推迟期间 option/mode 仍可能随
  // props 变化——这两个 ref 始终存住"当前渲染用的最新值",真正 init 时才
  // 不会拿到过期的初始 option。赋值放在下面的 effect(而不是渲染体)里,
  // 避免触发 react-hooks/refs(渲染期间不读写 ref);因为它每次 option/mode
  // 变化都会重跑,ResizeObserver 回调读到的仍然是上一次成功渲染后的最新值。
  const optionRef = useRef(option);
  const modeRef = useRef(mode);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let inited = false;
    const applyLatestOption = () => {
      // 导出模式关动画:html-to-image 截图时动画未完成会截到半截图形。
      const resolved: EChartsOption =
        modeRef.current === "export"
          ? { ...optionRef.current, animation: false }
          : optionRef.current;
      chartRef.current?.setOption(resolved, { notMerge: true });
    };
    const observer = new ResizeObserver((entries) => {
      if (!inited) {
        // 容器仍是 0×0(hidden tab 未打开、或还没完成首次布局):不 init,
        // 等下一次真正拿到非零尺寸的回调。ECharts 对 0 宽/高容器 init 会
        // 打印控制台警告,这是本次要消除的具体问题。
        const rect = entries[0]?.contentRect;
        if (!rect || rect.width <= 0 || rect.height <= 0) return;
        const chart = echarts.init(el);
        chartRef.current = chart;
        inited = true;
        // 容器变为可见的这一刻,必须用当时最新的 option 渲染一次,不能停
        // 留在 mount 那一刻的旧值(推迟 init 期间 option 可能已经更新过)。
        applyLatestOption();
        return;
      }
      chartRef.current?.resize();
    });
    observer.observe(el);
    return () => {
      observer.disconnect();
      // 容器全程 0×0、从未真正 init 过就被卸载(比如用户切换详情页 tab
      // 太快,某个 tab 从没被打开过):chartRef.current 仍是 null,跳过
      // dispose,不对不存在的实例操作。
      chartRef.current?.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    // 每次 option/mode 变化都把"最新值"记录下来,供上面挂载 effect 里的
    // ResizeObserver 回调在容器真正可见时读取(见 applyLatestOption)。
    optionRef.current = option;
    modeRef.current = mode;
    // 导出模式关动画:html-to-image 截图时动画未完成会截到半截图形。
    // chartRef.current 在容器还没拿到真实尺寸、图表尚未 init 时是 null,
    // 可选链在这里是安全的空操作——一旦 init 真正发生,会用最新 option
    // 显式 setOption 一次(见上面 applyLatestOption),不会漏更新。
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
