/**
 * 图表渲染模式(一份数据,两个渲染目标)。
 *
 * 背景:同一套 chart_specs 同时供比赛详情页(网站)和 Studio 卡片(短视频/小红书
 * 素材)使用,但两者对同一张图的要求是冲突的:
 *
 * - 网站:12-14px 字号、tooltip/筛选器/分段控件、动画开、高度自适应容器;
 * - 卡片:1080px 宽的导出舞台,12px 轴标签只占 1.1% 宽度,在手机上完全不可读;
 *   而且截图里的交互按钮全是死的。
 *
 * 这是此前 Studio 的 `charts` 场景从未被真正导出过的根因
 * (runtime/studio/ 下没有任何 `_charts_` 的 PNG)。
 *
 * 本模块只提供两套视觉 token,不改变任何数据口径 —— 图上画的东西完全一致,
 * 只是字号、粗细与留白按渲染目标切换。
 */

export type ChartMode = "interactive" | "export";

export type ChartTokens = {
  /** 坐标轴刻度标签字号 */
  axisFont: number;
  /** 图例字号 */
  legendFont: number;
  /** 数据标签(柱顶/点旁数值)字号 */
  labelFont: number;
  /** 柱状图柱宽 */
  barWidth: number;
  /** 折线拐点/散点尺寸 */
  symbolSize: number;
  /** 折线宽度 */
  lineWidth: number;
  /** 导出截图必须关动画,否则截到半截 */
  animation: boolean;
  /** grid 内边距倍率(大字号需要更多留白避免压边) */
  pad: number;
  /** 导出模式把数值直接标在图上(卡片没有 tooltip 可用) */
  alwaysShowValues: boolean;
};

export const CHART_TOKENS: Record<ChartMode, ChartTokens> = {
  interactive: {
    axisFont: 12,
    legendFont: 12,
    labelFont: 12,
    barWidth: 18,
    symbolSize: 6,
    lineWidth: 2,
    animation: true,
    pad: 1,
    alwaysShowValues: false,
  },
  export: {
    axisFont: 30,
    legendFont: 32,
    labelFont: 34,
    barWidth: 56,
    symbolSize: 18,
    lineWidth: 6,
    animation: false,
    pad: 2.6,
    alwaysShowValues: true,
  },
};

export function tokensFor(mode: ChartMode): ChartTokens {
  return CHART_TOKENS[mode];
}

/** grid 内边距按模式放大(导出模式字号大,需要更多轴外空间)。 */
export function scaleGrid(
  base: { left: number; right: number; top: number; bottom: number },
  mode: ChartMode,
): { left: number; right: number; top: number; bottom: number } {
  const { pad } = tokensFor(mode);
  return {
    left: Math.round(base.left * pad),
    right: Math.round(base.right * pad),
    top: Math.round(base.top * pad),
    bottom: Math.round(base.bottom * pad),
  };
}
