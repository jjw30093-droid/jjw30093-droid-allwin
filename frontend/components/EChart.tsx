"use client";

/**
 * 全站唯一图表封装(ECharts;宪法 §3.1 只保留一个图表库)。
 * 必须传 ariaSummary:图表的文字摘要(宪法 §11.2 图表要有文字摘要与空态)。
 *
 * mode="export" 用于 Studio 卡片(1080px 宽导出舞台):关动画、文字摘要放大到
 * 卡片可读字号。视觉 token 见 components/charts/chartMode.ts。
 */

import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts";
import type { EChartsOption } from "echarts";
import type { ChartMode } from "@/components/charts/chartMode";
import { ChartErrorBoundary } from "@/components/charts/ChartErrorBoundary";

type EChartProps = {
  option: EChartsOption;
  height?: number;
  ariaSummary: string;
  className?: string;
  mode?: ChartMode;
  /** 卡片自带说明文字时可关掉内置摘要,避免重复(a11y label 仍保留在容器上)。 */
  showSummary?: boolean;
  /** 2026-08-24 新增,可选:透传给底层 ECharts 实例的事件回调(目前只接
   * click)。借用 echarts-for-react 生态通行的 onEvents 形状,不自创一套。
   * 调用方业务代码自己抛出的异常不在下面两层错误边界覆盖范围内(那两层
   * 只兜渲染阶段和 setOption 调用阶段),调用方需自行保持简单防御性写法。 */
  onEvents?: Partial<Record<string, (params: unknown) => void>>;
};

/** 外层套 ChartErrorBoundary:兜住 `option` 本身在渲染阶段(如调用方
 * `useMemo(() => buildOption(...))`)抛出的异常——EChartInner 内部的
 * try/catch 只能兜住它自己发起的命令式 setOption 调用,兜不住 option 还没
 * 传进来之前就已经抛出的情况。两层合起来才是完整防线(见模块顶部说明)。 */
export function EChart(props: EChartProps) {
  return (
    <ChartErrorBoundary
      fallback={<p style={{ color: "var(--ink-3)", fontSize: 13.5 }}>图表暂时无法显示。</p>}
    >
      <EChartInner {...props} />
    </ChartErrorBoundary>
  );
}

function EChartInner({
  option,
  height = 280,
  ariaSummary,
  className,
  mode = "interactive",
  showSummary = true,
  onEvents,
}: EChartProps) {
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
  // 2026-08-24:同一惯用法存住最新的 onEvents。ECharts 的 chart.on() 不按
  // 函数引用去重——如果每次 onEvents 变化都重新 .on()(调用方大概率每次
  // 渲染传入新的内联函数,如 ShotMapChart 的 handleChartClick 闭包住
  // plotted),不配合精确 .off() 会导致监听器不断叠加、同一次点击触发 N 次
  // 回调。用 ref 间接层:.on() 只在下面挂载 effect 里绑定一次,绑定的是
  // "读 ref 取最新值"的稳定委托函数,onEvents 怎么变都不需要重新绑定。
  const onEventsRef = useRef(onEvents);
  // 2026-08-24:ECharts 配置异常(如势头图 visualMap 开区间在 6.x 上抛
  // `reading 'coord'`)是从 ResizeObserver 回调/useEffect 里发起的命令式
  // setOption 调用,不保证被 React 错误边界捕获(边界只可靠捕获渲染阶段的
  // 异常)——线上实测这类异常会一路冒泡到 Next.js 路由级错误边界,把整个
  // 比赛详情页拖垮成"页面出错了"。这里 try/catch 显式兜底,退化成这一张
  // 图表的空态,不影响页面其它部分;ChartErrorBoundary(见该文件)是第二层,
  // 兜住 option 计算本身(如 buildOption 内部)在渲染阶段抛出的异常。
  const [hasError, setHasError] = useState(false);

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
      try {
        chartRef.current?.setOption(resolved, { notMerge: true });
      } catch (err) {
        console.error("[EChart] setOption 异常,已降级为空态:", err);
        // 与下面第二个 effect 同一处理(见那里的注释):推到微任务,不在
        // ResizeObserver 回调同步栈里直接 setState。
        queueMicrotask(() => setHasError(true));
      }
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
        // 固定绑定一次委托监听器,不管调用方有没有传 onEvents.click——
        // 委托函数内部才判断是否存在,不存在就是空操作。见上面 onEventsRef
        // 的注释:.on() 只在这里调用一次,onEvents 怎么变都不用重新绑定。
        chart.on("click", (params) => onEventsRef.current?.click?.(params));
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
    onEventsRef.current = onEvents;
    // 导出模式关动画:html-to-image 截图时动画未完成会截到半截图形。
    // chartRef.current 在容器还没拿到真实尺寸、图表尚未 init 时是 null,
    // 可选链在这里是安全的空操作——一旦 init 真正发生,会用最新 option
    // 显式 setOption 一次(见上面 applyLatestOption),不会漏更新。
    const resolved: EChartsOption =
      mode === "export" ? { ...option, animation: false } : option;
    try {
      chartRef.current?.setOption(resolved, { notMerge: true });
    } catch (err) {
      console.error("[EChart] setOption 异常,已降级为空态:", err);
      // react-hooks/set-state-in-effect:不在 effect 体内同步 setState(会
      // 触发级联渲染)——推到微任务,行为上仍是"这一帧结束前尽快更新",只是
      // 不再算作这个 effect 自己触发的同步重渲染。
      queueMicrotask(() => setHasError(true));
    }
  }, [option, mode, onEvents]);

  if (hasError) {
    return <p style={{ color: "var(--ink-3)", fontSize: 13.5 }}>图表暂时无法显示。</p>;
  }

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
