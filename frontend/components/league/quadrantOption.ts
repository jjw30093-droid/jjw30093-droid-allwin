/**
 * 联赛球队象限图的 ECharts option 构造器(纯函数,CLAUDE.md §11.3 要求可独立
 * headless 渲染冒烟,见 frontend/tests/chart-render-smoke.test.ts)。
 *
 * series 自下而上:
 *   hit    透明命中层(交互模式)——直径按避让实际达成的间距,手机点得中又不偷邻居
 *   ring   选中光环(silent)——描边用 c.ink,不用品牌金:金色压在白底只有 1.74:1
 *   crest  队徽主层——symbol: image://,无队徽退回象限色圆点 + 队名标签(见下)
 *
 * 2026-09-09:曾经加过一个"引线 + 真实坐标小圆点"的 leader series,位移超阈值
 * 的徽章会连一条线指回真实坐标——用户反馈"图上不该有点,坐标点全部应该是
 * 队徽",这条线和它末端的小圆点本身就是"点",与"用队徽代替散点"的初衷矛盾,
 * 已整体移除。避让位移本身有上限(CREST.MAX_SHIFT_RATIO),偏移量不大;
 * 精确数值靠点击后的详情面板与 tooltip,不再用视觉标注真实坐标。
 *
 * ⚠ 所有不带 pt 的 series 必须 silent:true,否则共享的 tooltip.formatter 会对
 *   没有 .pt 字段的数据点抛异常(ShotMapChart.tsx:215-217 记录的坑;渲染冒烟
 *   测试不模拟 hover,抓不到,这里手动规避)。
 * ⚠ 队名标签不能只靠 ECharts 的 labelLayout.hideOverlap:那只比较"标签 vs
 *   标签",不知道画布上还有队徽图片——一个标签能避开所有其它标签,却仍能
 *   整个盖在旁边一支球队的队徽上(2026-09 用户反馈)。真正的可见性由
 *   resolveLabelVisibility 用真实绘制坐标做"标签矩形 vs 队徽圆"相交测试
 *   算出来,hideOverlap 只负责剩下的"标签 vs 标签"微调。
 */

import type { EChartsOption } from "echarts";
import type { ChartColors } from "@/components/charts/useChartColors";
import { scaleGrid, tokensFor, type ChartMode } from "@/components/charts/chartMode";
import {
  CREST,
  crestSymbol,
  hitSizeFor,
  resolveLabelVisibility,
  type AxisRange,
  type CrestLayout,
  type Grid,
  type LabelCandidate,
} from "@/components/charts/crestQuadrantLayout";
import { fmt, quadrantOf, type Pt, type View } from "./quadrantViews";

export type QuadrantOptionArgs = {
  view: View;
  /** 渲染顺序 == dataIndex 顺序,点击回查靠它 */
  pts: Pt[];
  mx: number;
  my: number;
  colors: ChartColors;
  /** 要显示队名的球队:outlierNames ∪ 无队徽 ∪ 选中 */
  labelled: Set<string>;
  crestSize: number;
  /** null = 宽度尚未测得,退化为零偏移(最坏是徽章像旧圆点一样重叠,不是坏图) */
  layout: CrestLayout | null;
  xr: AxisRange;
  yr: AxisRange;
  grid: Grid;
  /** 0~2 个,pts 下标 */
  selectedIndexes: number[];
  mode?: ChartMode;
};

export function quadColors(c: ChartColors): [string, string, string, string] {
  // 0(两项都好)= 绿, 2(两项都差)= 红, 1/3(一好一差)= 青
  return [c.win, c.teal, c.loss, c.teal];
}

export function buildQuadrantOption(args: QuadrantOptionArgs): EChartsOption {
  const { view, pts, mx, my, colors: c, labelled, crestSize, layout, xr, yr, selectedIndexes } = args;
  const mode = args.mode ?? "interactive";
  const tk = tokensFor(mode);
  const grid = mode === "export" ? scaleGrid(args.grid, mode) : args.grid;
  const lowY = view.y.lowerIsBetter === true;
  const quad = (p: Pt) => quadrantOf(p, mx, my, lowY);
  const QUAD_COLOR = quadColors(c);
  const hasSelection = selectedIndexes.length > 0;
  const isSelected = (i: number) => selectedIndexes.includes(i);
  const offsetOf = (i: number): [number, number] => layout?.offset[i] ?? [0, 0];
  const showAllLabels = mode === "export";

  // 队名标签会不会盖住旁边的队徽?只有拿到真实布局(layout 非空)时才能算——
  // 首帧宽度尚未测得时退回"想显示就显示",反正只持续一帧,不是错误状态。
  const safeLabelled: Set<string> = layout
    ? (() => {
        const candidates: LabelCandidate[] = pts.map((p, i) => {
          const base = layout.base[i];
          const [dx, dy] = offsetOf(i);
          const sel = isSelected(i);
          const want = showAllLabels || labelled.has(p.name);
          return {
            cx: base.px + dx,
            cy: base.py + dy,
            radius: (sel ? crestSize * CREST.SELECTED_SCALE : crestSize) / 2,
            text: want ? p.name : null,
          };
        });
        const visible = resolveLabelVisibility(candidates, { fontSize: tk.labelFont });
        return new Set(pts.filter((_, i) => visible[i]).map((p) => p.name));
      })()
    : labelled;

  const crestData = pts.map((p, i) => {
    const sel = isSelected(i);
    const opacity = !hasSelection ? CREST.BASE_OPACITY : sel ? 1 : CREST.DIM_OPACITY;
    const base = {
      value: [p.x, p.y],
      pt: p,
      symbol: crestSymbol(p.crestUrl),
      symbolSize: sel ? Math.round(crestSize * CREST.SELECTED_SCALE) : crestSize,
      symbolOffset: offsetOf(i),
    };
    return p.crestUrl
      ? { ...base, itemStyle: { opacity } }
      : {
          ...base,
          itemStyle: {
            color: QUAD_COLOR[quad(p)],
            borderColor: c.surface,
            borderWidth: 1.5,
            opacity,
          },
        };
  });

  // 恰好选中 1 队时画到两轴的虚线并标注精确数值——放大的徽章会盖住真实点。
  // 选中 2 队不画:4 条线糊成一团,面板里有精确数值。
  const dropLines =
    selectedIndexes.length === 1
      ? (() => {
          const s = pts[selectedIndexes[0]];
          if (!s) return [];
          // x 轴线永远在绘图区底部;y 轴 inverse 时底部是 yr.max
          const yEdge = lowY ? yr.max : yr.min;
          return [
            [
              { coord: [s.x, s.y], label: { show: true, position: "end", formatter: fmt(s.x, view.x) } },
              { coord: [s.x, yEdge] },
            ],
            [
              { coord: [s.x, s.y], label: { show: true, position: "end", formatter: fmt(s.y, view.y) } },
              { coord: [xr.min, s.y] },
            ],
          ];
        })()
      : [];

  const series: NonNullable<EChartsOption["series"]> = [];

  if (mode === "interactive") {
    series.push({
      name: "hit",
      type: "scatter",
      z: 1,
      silent: false,
      symbol: "circle",
      symbolSize: hitSizeFor(layout, crestSize),
      itemStyle: { color: "transparent" },
      emphasis: { disabled: true },
      tooltip: { show: false },
      data: pts.map((p, i) => ({ value: [p.x, p.y], pt: p, symbolOffset: offsetOf(i) })),
    });
  }

  if (hasSelection) {
    series.push({
      name: "ring",
      type: "scatter",
      silent: true,
      z: 2,
      symbol: "circle",
      data: selectedIndexes
        .map((i) => pts[i] && { value: [pts[i].x, pts[i].y], symbolOffset: offsetOf(i) })
        .filter(Boolean),
      symbolSize: Math.round(crestSize * CREST.SELECTED_SCALE) + 8,
      itemStyle: { color: c.surface, borderColor: c.ink, borderWidth: 2 },
    });
  }

  series.push({
    name: "crest",
    type: "scatter",
    z: 3,
    symbolKeepAspect: true,
    data: crestData,
    label: {
      show: true,
      // 标在点正上方而不是右侧:右侧的队名到了图右边缘会被裁掉
      position: "top",
      distance: 5,
      color: c.ink2,
      fontSize: tk.labelFont,
      formatter: (p: unknown) => {
        const d = (p as { data: { pt: Pt } }).data.pt;
        return safeLabelled.has(d.name) ? d.name : "";
      },
    },
    // safeLabelled 已经排除了"会压住别的队徽"的标签;这里的 moveOverlap/
    // hideOverlap 只负责剩下的"标签 vs 标签"微调(上下错开,仍压在一起的才隐藏)。
    // 选中队的名字不因为跟别的标签打架被隐藏,但仍受 safeLabelled 的队徽碰撞检测约束。
    labelLayout: (p) => ({
      moveOverlap: "shiftY" as const,
      hideOverlap: !isSelected(p.dataIndex ?? -1),
    }),
    markLine: {
      silent: true,
      symbol: "none",
      lineStyle: { color: c.ink2, type: "dashed", opacity: 0.45 },
      label: { show: false, color: c.ink, fontSize: tk.labelFont },
      data: [{ xAxis: mx }, { yAxis: my }, ...dropLines] as never,
    },
  });

  return {
    grid,
    xAxis: {
      type: "value",
      name: view.x.label,
      nameLocation: "middle",
      nameGap: 26,
      nameTextStyle: { color: c.ink2, fontSize: tk.axisFont },
      // 显式 nice 端点 + interval:刻度干净且像素可复算(见 crestQuadrantLayout.ts 头注释)
      min: xr.min,
      max: xr.max,
      interval: xr.interval,
      axisLabel: { color: c.ink2, fontSize: tk.axisFont },
      splitLine: { lineStyle: { opacity: 0.1 } },
    },
    yAxis: {
      type: "value",
      name: view.y.label,
      // inverse 会把轴的 end 翻到底部,和 x 轴名撞在一起 —— 反转时改用 start,
      // 让轴名永远停在图的左上角。
      nameLocation: lowY ? "start" : "end",
      nameGap: 12,
      nameTextStyle: { color: c.ink2, fontSize: tk.axisFont, align: "left" },
      // 预期失球越少越好 → 反转,让"好"永远在上方
      inverse: lowY,
      min: yr.min,
      max: yr.max,
      interval: yr.interval,
      axisLabel: { color: c.ink2, fontSize: tk.axisFont },
      splitLine: { lineStyle: { opacity: 0.1 } },
    },
    tooltip: {
      confine: true,
      trigger: "item",
      // 桌面保留 hover;手机(touch 不产生 mousemove)不与点击选中打架
      triggerOn: "mousemove",
      formatter: (p: unknown) => {
        const d = (p as { data?: { pt?: Pt } }).data?.pt;
        if (!d) return "";
        const q = view.quadrants[quad(d)];
        return (
          `<b>${d.name}</b>（${q}）<br/>` +
          `${view.x.label} ${fmt(d.x, view.x)}（联赛均值 ${fmt(mx, view.x)}）<br/>` +
          `${view.y.label} ${fmt(d.y, view.y)}（联赛均值 ${fmt(my, view.y)}）` +
          (d.mp != null ? `<br/>样本 ${d.mp} 场` : "") +
          `<br/><span style="opacity:.7">点击查看排名与更多指标</span>`
        );
      },
    },
    series,
  };
}
