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
 *
 * 2026-09-16 交互性(站长要求"点击头像会放大等等,有交互效果"):
 * - 每个 series 都带**稳定的 `id`**(不再只靠数组位置隐式区分)。这不是
 *   顺手加的——`ring` 只在 hasSelection 时才被 push 进 series 数组,选中数
 *   0↔1 切换的瞬间会让它后面的 `crest` 在数组里的下标跟着挪动。ECharts
 *   在 `notMerge:true` 下默认按"同类型 + 数组下标"匹配前后两次 setOption
 *   的同一个 series 来算过渡动画,下标一旦跳动,`crest` 就会被当成"换了一个
 *   新 series",直接跳变而不是平滑放大——这恰好发生在用户点第一下、最该
 *   看见放大效果的那一刻。给三个 series 都挂 `id` 后,匹配改成按 id 走,
 *   不再受数组位置影响。
 * - `crest` 新增 `cursor:'pointer'`(未点击前就能看出"这是可点的")与
 *   `emphasis:{scale:1.12}`(悬停时先给一个比点击态 1.25x 更克制的预览性
 *   放大,鼠标移开自动复位,不需要额外的 React 状态或重新计算避让布局——
 *   `emphasis` 是 ECharts 声明式状态,内部用 zrender 直接处理,不经过
 *   `buildQuadrantOption` 重新调用)。`stateAnimation` 单独给这个"悬停/
 *   选中"级别的形变配一段过渡(250ms cubicOut),不牵动全局
 *   `animationDurationUpdate`——那个全局值同时管着切视角/切位置时坐标点
 *   的整体重新布局,调它会让无关的更新也变得忽快忽慢。
 * - `hit` 层本身透明、`emphasis` 也保持关闭(放大一个看不见的圆没有意义),
 *   但同样给 `cursor:'pointer'`——它比 `crest` 的可见头像大一圈(手机触控
 *   冗余),这一圈范围鼠标移上去也该看出能点,不能只在头像本体范围内才变手型。
 */

import type { EChartsOption } from "echarts";
import { hexToRgba, type ChartColors } from "@/components/charts/useChartColors";
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
import { axisLabel, dirsOf, fmt, quadrantOf, type AxisLike, type Pt } from "./quadrantViews";

/** 2026-09-15 起泛型化,球队/球员象限图共用同一个 option 构造器(零行为
 *  变化,好过复制这 286 行):`P` 是渲染的点类型(球队用现有 Pt,球员用
 *  PlayerPt),只要求 {key,name,x,y,mp?} 这个最小形状;`symbolUrlOf` 取代
 *  直接读 `p.crestUrl`,球队传 `(p) => p.crestUrl`、球员传
 *  `(p) => p.avatarUrl`,不强行统一字段名。`view` 同样放宽成结构类型
 *  (ViewLike),不要求完整 View/MetricDef.value(那部分是取数逻辑,
 *  option 构造器从不调用它)。 */
export type QuadrantPoint = { key: string; name: string; x: number; y: number; mp?: number | null };
export type ViewLike = {
  title: string;
  x: AxisLike;
  y: AxisLike;
  quadrants: [string, string, string, string];
};

export type QuadrantOptionArgs<P extends QuadrantPoint = Pt> = {
  view: ViewLike;
  /** 渲染顺序 == dataIndex 顺序,点击回查靠它 */
  pts: P[];
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
  /** 该点用哪张图当坐标符号(队徽/头像),null 时退回象限色圆点。
   *  默认读 `(p as Pt).crestUrl`——球队侧调用点不用传,球员侧必传
   *  `(p) => p.avatarUrl`。 */
  symbolUrlOf?: (p: P) => string | null | undefined;
};

export function quadColors(c: ChartColors): [string, string, string, string] {
  // 0(两项都好)= 绿, 2(两项都差)= 红, 1/3(一好一差)= 青
  return [c.win, c.teal, c.loss, c.teal];
}

export function buildQuadrantOption<P extends QuadrantPoint = Pt>(
  args: QuadrantOptionArgs<P>,
): EChartsOption {
  const { view, pts, mx, my, colors: c, labelled, crestSize, layout, xr, yr, selectedIndexes } = args;
  const symbolUrlOf = args.symbolUrlOf ?? ((p: P) => (p as unknown as Pt).crestUrl);
  const mode = args.mode ?? "interactive";
  const tk = tokensFor(mode);
  const grid = mode === "export" ? scaleGrid(args.grid, mode) : args.grid;
  const dirs = dirsOf(view);
  const lowY = dirs.y === true;
  const quad = (p: P) => quadrantOf(p, mx, my, dirs);
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
    const symbolUrl = symbolUrlOf(p);
    const base = {
      value: [p.x, p.y],
      pt: p,
      symbol: crestSymbol(symbolUrl),
      symbolSize: sel ? Math.round(crestSize * CREST.SELECTED_SCALE) : crestSize,
      symbolOffset: offsetOf(i),
    };
    return symbolUrl
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

  // 四象限名直接画在图上(2026-09-16 真实反馈:站长看完"门将出球"视角说
  // "并没有在象限图中看到有说明,例如是什么类型的门将"——四象限名此前只在
  // tooltip/图例列表/点击后的详情面板里出现,不看图上任何一个具体点、
  // 不滚动到图表下方就完全看不到"这是什么类型")。用 4 个不可交互的
  // "隐形散点+文字标签"分别定位到 4 个几何角(留一点内缩,避免贴边被裁),
  // 每个角对应的象限用真实的 quadrantOf 在该角的数据坐标上算一次,不是
  // 凭"左上/右上"猜——这样无论某个视角的 x/y 方向如何(dirsOf 决定"好"在
  // 数值大还是小的一侧),角标文字永远和图例、tooltip 里的象限名对得上。
  const cornerInsetX = (xr.max - xr.min) * 0.04;
  const cornerInsetY = (yr.max - yr.min) * 0.04;
  const cornerLabelData = (
    [
      [xr.max, yr.max],
      [xr.min, yr.max],
      [xr.min, yr.min],
      [xr.max, yr.min],
    ] as const
  ).map(([cornerX, cornerY]) => {
    const idx = quadrantOf({ x: cornerX, y: cornerY }, mx, my, dirs);
    const px = cornerX === xr.max ? cornerX - cornerInsetX : cornerX + cornerInsetX;
    const py = cornerY === yr.max ? cornerY - cornerInsetY : cornerY + cornerInsetY;
    const align: "left" | "right" = cornerX === xr.max ? "right" : "left";
    const verticalAlign: "top" | "bottom" = cornerY === yr.max ? "top" : "bottom";
    return {
      value: [px, py],
      label: {
        show: true,
        formatter: view.quadrants[idx],
        color: QUAD_COLOR[idx],
        fontWeight: 700 as const,
        fontSize: tk.axisFont,
        align,
        verticalAlign,
      },
    };
  });
  series.push({
    id: "quadrant-labels",
    name: "quadrant-labels",
    type: "scatter",
    silent: true,
    tooltip: { show: false },
    z: 0,
    // symbol:'none' 在部分 ECharts 版本里会连带压掉数据点自己的 label——
    // 用 symbolSize:0(而不是 symbol:'none')保留点位机制,只是尺寸为零,
    // 这样每个点自带的 label 仍然正常渲染。
    symbol: "circle",
    symbolSize: 0,
    data: cornerLabelData,
  });

  if (mode === "interactive") {
    series.push({
      id: "hit",
      name: "hit",
      type: "scatter",
      z: 1,
      silent: false,
      cursor: "pointer",
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
      id: "ring",
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
    id: "crest",
    name: "crest",
    type: "scatter",
    z: 3,
    cursor: "pointer",
    // 悬停时先给一个比点击选中(1.25x)更克制的预览性放大,鼠标移开自动
    // 复位——ECharts 声明式状态,不经过本函数重新计算,cheap 且不影响
    // 避让布局。选中态的 1.25x 走的是另一条路径(symbolSize 直接写进
    // crestData,靠下面的 stateAnimation 补一段过渡),两者不冲突:一个是
    // "普通态本身多大",一个是"普通态之上悬停再多放大一点"。
    emphasis: { scale: 1.12 },
    // 只管"普通/悬停/选中"这几个视觉状态之间怎么过渡,不碰全局
    // animationDurationUpdate(那个还管着切视角/切位置时坐标点的整体
    // 重新布局,调它会连累无关更新一起变忽快忽慢)。
    stateAnimation: { duration: 250, easing: "cubicOut" },
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
    // 四象限底色:纯视觉棋盘格,不挂任何"好/坏"语义(颜色只取 c.ink 的极低
    // 透明度,两个主题下都是"比背景略深一点"而不是某种判断色)——只是让
    // "这一条是四个区"这件事一眼可辨,不用靠脑内延长两条虚线。对角两块上色,
    // 另外对角两块透明,像棋盘格一样纯粹分区,不隐含"这个角比那个角好"。
    markArea: {
      silent: true,
      itemStyle: { color: hexToRgba(c.ink, 0.035) },
      data: [
        [{ coord: [xr.min, yr.min] }, { coord: [mx, my] }],
        [{ coord: [mx, my] }, { coord: [xr.max, yr.max] }],
      ] as never,
    },
  });

  return {
    grid,
    xAxis: {
      type: "value",
      name: axisLabel(view.x),
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
      name: axisLabel(view.y),
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
      // 默认 ECharts 提示框样式(直角、细描边)和站内卡片系统的圆角/阴影
      // 语言对不上——这里显式接管容器样式,颜色仍从 c(useChartColors)取,
      // 深浅主题自动跟随,不新引入颜色。
      backgroundColor: c.surface,
      borderColor: c.grey,
      borderWidth: 1,
      borderRadius: 10,
      padding: [10, 13],
      extraCssText: "box-shadow:0 6px 20px rgba(0,0,0,.14); line-height:1.65;",
      textStyle: { color: c.ink2, fontSize: 12.5 },
      formatter: (p: unknown) => {
        const d = (p as { data?: { pt?: P } }).data?.pt;
        if (!d) return "";
        const q = view.quadrants[quad(d)];
        return (
          `<div style="font-weight:700;font-size:13.5px;color:${c.ink};margin-bottom:2px;">` +
          `${d.name}<span style="margin-left:6px;font-weight:500;font-size:11.5px;color:${c.teal};">${q}</span></div>` +
          `${view.x.label} <b style="color:${c.ink};">${fmt(d.x, view.x)}</b>（联赛均值 ${fmt(mx, view.x)}）<br/>` +
          `${view.y.label} <b style="color:${c.ink};">${fmt(d.y, view.y)}</b>（联赛均值 ${fmt(my, view.y)}）` +
          (d.mp != null ? `<br/>样本 ${d.mp} 场` : "") +
          `<br/><span style="opacity:.7">点击查看排名与更多指标</span>`
        );
      },
    },
    series,
  };
}
