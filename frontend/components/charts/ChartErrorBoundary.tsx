"use client";

/**
 * 图表错误边界(2026-08-24)。
 *
 * 起因:势头图的 visualMap 开区间配置在 ECharts 6 上会抛
 * `Cannot read properties of undefined (reading 'coord')`——线上实测这个异常
 * 会一路冒泡到 Next.js 路由级错误边界,把整个比赛详情页变成"页面出错了",
 * 而不是那一张图表退化成空态(见 CLAUDE.md §11.3)。
 *
 * 必须是 class 组件——`componentDidCatch`/`getDerivedStateFromError` 没有
 * hooks 等价物。只用于渲染阶段抛出的异常(如 `buildOption` 在 useMemo 里
 * 抛出);ECharts `setOption` 是在 useEffect/ResizeObserver 回调里调用的
 * 命令式 API,这类异常不保证被 React 错误边界捕获,`EChart.tsx` 自己用
 * try/catch 兜底(见该文件)。两层合起来才是完整防线。
 */

import { Component, type ReactNode } from "react";

export class ChartErrorBoundary extends Component<
  { children: ReactNode; fallback?: ReactNode },
  { hasError: boolean }
> {
  constructor(props: { children: ReactNode; fallback?: ReactNode }) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error: unknown) {
    // 只静默降级,不拖垮整页;留一行日志供线上排查,不吞掉信号。
    console.error("[ChartErrorBoundary] 图表渲染异常,已降级为空态:", error);
  }

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback ?? (
          <p style={{ color: "var(--ink-3)", fontSize: 13.5 }}>图表暂时无法显示。</p>
        )
      );
    }
    return this.props.children;
  }
}
