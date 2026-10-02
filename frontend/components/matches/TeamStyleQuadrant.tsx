"use client";

/**
 * 模块二:球队风格定位(象限图)。
 *
 * 沿用 components/league/TeamQuadrantChart.tsx 的模式:多视角 pill、联赛均值虚线切四象限、
 * 中文象限名、有效点 < 4 时该视角禁用并说明原因(不静默回退、不补 0)。
 * 与联赛页的差别:这里要回答的是「这两队在联赛分布里站哪」,不是给全联赛排序——
 * 所以默认选中本场主客两队(高亮 + 队名),其余球队降为半透明队徽背景。
 *
 * 2026-09-09 改造:坐标点改用真实队徽(DTO 新增 crest_url,后端
 * team_style_preview.py 同 TeamRef 一套 resolve_team_crest_url;无本地队徽退回
 * 主/客/灰圆点 + 队名),点击任一队徽可换成对比它——最多两队,第三支按 FIFO
 * 顶掉最早的;点空白 / Esc / 「恢复本场两队」回到主客对比。避让布局与命中层
 * 与联赛页共用 components/charts/crestQuadrantLayout.ts(坐标点全部是队徽,
 * 不画任何引线/圆点标注真实坐标——精确数值靠点击后的详情面板与 tooltip)。
 *
 * 图表走全站唯一封装 components/EChart.tsx,ariaSummary 必填(CLAUDE.md §11.2)。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type { EChartsOption } from "echarts";
import { EChart } from "@/components/EChart";
import { TeamBadge } from "@/components/teams/TeamBadge";
import type { ChartColors } from "@/components/charts/useChartColors";
import type { TeamBrandColor, TeamColorPair } from "@/components/charts/matchTeamColors";
import { useMatchColors } from "@/components/charts/useMatchColors";
import {
  CREST,
  QUADRANT_GRID,
  crestSizeFor,
  crestSymbol,
  hitSizeFor,
  layoutCrests,
  niceAxisRange,
  resolveLabelVisibility,
  type AxisRange,
  type CrestLayout,
  type Grid,
  type LabelCandidate,
  type PlotBox,
} from "@/components/charts/crestQuadrantLayout";
import { competitionRank } from "@/components/league/teamMetrics";
import {
  dirsOf,
  outlierNames,
  plotSet,
  quadrantOf,
  viewById,
  type Pt,
} from "@/components/league/quadrantViews";
import { buildQuadrantOption } from "@/components/league/quadrantOption";
import styles from "./MatchDataModules.module.css";
import panelStyles from "./TeamStyleQuadrant.module.css";
import pageStyles from "@/app/matches/[matchId]/match-detail.module.css";
import type { components } from "@/lib/api-types";
import type { TeamSeasonStatRow } from "@/lib/api-v1";

type StyleView = components["schemas"]["MatchPreviewStyleViewDTO"];
type StylePoint = components["schemas"]["MatchPreviewStylePointDTO"];
type PlottedPoint = StylePoint & { x: number; y: number };

/**
 * 「攻守 xG」视角(2026-09-28 起改为复用球队页 both-ends 视角与
 * team_season_stats 数据,不再由 team_style_preview.py 产出——
 * scripts/audit/quadrant_audit.py 发现它与球队页逐队数值 Spearman
 * x-x/y-y 均 r=1.0,是同一份数据的重复实现)。id 沿用旧值,保持
 * DOM/测试里已有的引用稳定;tab 文案与球队页同步为「攻守 xG」。
 */
const REAL_VIEW_ID = "xg-for-against";
const REAL_VIEW = viewById("both-ends");

/** 统一形状:legacy(poss-fastbreak/cross-box,来自 team_style_preview.py 的
 * 近 N 场滚动)与 real(攻守 xG,来自球队页 both-ends + team_season_stats
 * 的整赛季口径)在这一层统一成同一套字段,下面的选中态/表格/ariaSummary/
 * 图例代码不用为两种来源各写一份——只有"怎么算象限下标"和"怎么建
 * ECharts option"两处仍然分叉,因为两边底层 view 对象的下标约定不同
 * (quadrantIndex 原始高低 vs quadrantOf 好/坏,见文件顶部注释),不能
 * 靠数值换算硬拉平,那正是 2026-09-16 门将视角标签写反那次真实事故的
 * 错误模式。 */
type NormalizedView = {
  id: string;
  tab: string;
  title: string;
  x_label: string;
  y_label: string;
  digits: number;
  quadrants: [string, string, string, string];
  y_lower_is_better: boolean;
  points: PlottedPoint[];
  isReal: boolean;
  /** 仅 isReal=true 时有值:option 构造与详情面板都需要原始 Pt(带 key/teamId)。 */
  realPts?: Pt[];
  windowLabel: string;
  /** legacy(近 N 场滚动)取自 StyleView.window;real(整赛季)为 null,
   *  渲染时改用固定文案,不写一个不存在的"至多 N 场"。 */
  window: number | null;
};

function quadOfFor(nv: NormalizedView, p: { x: number; y: number }, mx: number, my: number): number {
  return nv.isReal ? quadrantOf(p, mx, my, dirsOf(REAL_VIEW)) : quadrantIndex(p, mx, my);
}

function usable(v: NormalizedView) {
  return v.points.filter((p) => p.x != null && p.y != null).length >= 4;
}

const mean = (nums: number[]) => nums.reduce((a, b) => a + b, 0) / nums.length;

/**
 * 象限下标按**原始数值高低**(不是好坏)固定:0=x高y高 / 1=x高y低 /
 * 2=x低y高 / 3=x低y低——与 backend/queries/team_style_preview.py 的
 * `_TEAM_STAT_VIEWS` 下标约定一一对应,不随 `y_lower_is_better` 改变。
 *
 * "y 越低越好"的方向语义由**后端提供的 `quadrants` 文案本身**承载
 * (例如 xg-for-against 视角把 idx0="对攻型" 而不是"攻守兼备")——
 * 前端不需要、也不应该用 y_lower_is_better 去反转这里的算术,否则会
 * 和后端文案的下标约定重复处理方向,一旦两边不同步就会静默错位。
 * `y_lower_is_better` 仍然端到端传播到这里(DTO → api-types → StyleView),
 * 是为了让调用方(以及未来的展示逻辑)能读到方向语义本身,不只是猜文案顺序。
 */
export function quadrantIndex(
  p: { x: number; y: number },
  mx: number,
  my: number,
): 0 | 1 | 2 | 3 {
  const xHi = p.x >= mx;
  const yHi = p.y >= my;
  if (xHi && yHi) return 0;
  if (xHi && !yHi) return 1;
  if (!xHi && yHi) return 2;
  return 3;
}

export type StyleQuadrantOpts = {
  crestSize?: number;
  layout?: CrestLayout | null;
  /** 显式轴范围(与 layout 配套);缺省时退回 scale:true 的自动范围 */
  xr?: AxisRange;
  yr?: AxisRange;
  grid?: Grid;
  /** 高亮的球队 id(0~2 个);缺省 = 本场主客 */
  selectedIds?: number[];
  /** 2026-09 移动端修复:窄屏下非选中球队从队徽降级为小圆点(无队名),
   * 避让压力立刻消失——375px 屏实测绘图区只有约 241×242px 要塞 20 个
   * 队徽,人均可用空间 2916px² 但队徽视觉位置几乎完全由避让算法主导。
   * 不改共享的 `crestQuadrantLayout.ts`(联赛页复用同一份布局数学),
   * 避让偏移量仍按统一 crestSize 计算——降级后的圆点因此比严格必要的间距
   * 更松,是刻意的保守选择,不产生重叠风险。 */
  compactOthers?: boolean;
};

/** 窄屏降级圆点的固定尺寸与颜色——不复用 CREST 常量(那些是队徽尺寸),
 * 这里要的是明显更小、明显"次要"的视觉权重。 */
const COMPACT_DOT_SIZE = 8;

/** 2026-08-24 抽出为可独立渲染冒烟测试的纯函数(CLAUDE.md §11.3)。 */
/** buildOption 只读这四个字段;放宽成结构类型后 NormalizedView(legacy 分支)
 *  和原始 StyleView 都能直接传,不用为了适配类型再包一层转换对象。 */
export type LegacyViewLike = Pick<StyleView, "x_label" | "y_label" | "digits" | "quadrants">;

export function buildOption(
  view: LegacyViewLike,
  pts: PlottedPoint[],
  mx: number,
  my: number,
  homeTeamId: number,
  awayTeamId: number,
  c: ChartColors,
  opts: StyleQuadrantOpts = {},
): EChartsOption {
  const sideOf = (p: StylePoint) =>
    p.team_id === homeTeamId ? "home" : p.team_id === awayTeamId ? "away" : null;
  const fmt = (v: number) => v.toFixed(view.digits);
  const quadOf = (p: { x: number; y: number }) => view.quadrants[quadrantIndex(p, mx, my)];
  const selectedIds = opts.selectedIds ?? [homeTeamId, awayTeamId];
  const selectedIndexes = pts
    .map((p, i) => (selectedIds.includes(p.team_id) ? i : -1))
    .filter((i) => i >= 0);
  const isSelected = (i: number) => selectedIndexes.includes(i);
  const crestSize = opts.crestSize ?? 22;
  const layout = opts.layout ?? null;
  const grid = opts.grid ?? QUADRANT_GRID;
  const offsetOf = (i: number): [number, number] => layout?.offset[i] ?? [0, 0];
  const colorOf = (side: "home" | "away" | null) =>
    side === "home" ? c.teal : side === "away" ? c.navy : c.grey;

  const compactOthers = opts.compactOthers ?? false;

  const crestData = pts.map((p, i) => {
    const side = sideOf(p);
    const sel = isSelected(i);
    const opacity = sel ? 1 : CREST.DIM_OPACITY;
    if (compactOthers && !sel) {
      // 窄屏降级:非本场/非对比中的球队一律小圆点,不加载队徽图片,
      // 也不区分主客配色(它们本来就不是主客队)。
      return {
        value: [p.x, p.y],
        pt: p,
        side,
        symbol: "circle",
        symbolSize: COMPACT_DOT_SIZE,
        symbolOffset: offsetOf(i),
        itemStyle: { color: c.grey, opacity: 0.7, borderColor: "transparent" },
      };
    }
    const base = {
      value: [p.x, p.y],
      pt: p,
      side,
      symbol: crestSymbol(p.crest_url),
      symbolSize: sel ? Math.round(crestSize * CREST.SELECTED_SCALE) : crestSize,
      symbolOffset: offsetOf(i),
    };
    return p.crest_url
      ? { ...base, itemStyle: { opacity } }
      : {
          ...base,
          itemStyle: {
            color: colorOf(side),
            opacity,
            borderColor: c.surface,
            borderWidth: 1.5,
          },
        };
  });

  // 队名标签会不会盖住旁边的队徽?只有拿到真实布局时才能算,见
  // components/league/quadrantOption.ts 头部注释——同一个 bug、同一套修法。
  // 首帧宽度尚未测得时退回"想显示就显示",只持续一帧。
  const labelVisible: boolean[] = layout
    ? resolveLabelVisibility(
        pts.map((p, i): LabelCandidate => {
          const [dx, dy] = offsetOf(i);
          const sel = isSelected(i);
          // compactOthers 时非选中一律不给名字——降级圆点本来就是"匿名背景",
          // 给名字会把 20 支球队的名字全部挤上屏,违背降级的初衷。
          const want = sel || (!compactOthers && !p.crest_url);
          return {
            cx: layout.base[i].px + dx,
            cy: layout.base[i].py + dy,
            radius: (sel ? crestSize * CREST.SELECTED_SCALE : crestSize) / 2,
            text: want ? p.name : null,
          };
        }),
        { fontSize: 11.5, distance: 6 },
      )
    : pts.map(() => true);

  const series: NonNullable<EChartsOption["series"]> = [];

  series.push({
    name: "hit",
    type: "scatter",
    z: 1,
    symbol: "circle",
    symbolSize: hitSizeFor(layout, crestSize),
    itemStyle: { color: "transparent" },
    emphasis: { disabled: true },
    tooltip: { show: false },
    data: pts.map((p, i) => ({ value: [p.x, p.y], pt: p, symbolOffset: offsetOf(i) })),
  });

  if (selectedIndexes.length) {
    series.push({
      name: "ring",
      type: "scatter",
      silent: true,
      z: 2,
      symbol: "circle",
      symbolSize: Math.round(crestSize * CREST.SELECTED_SCALE) + 8,
      data: selectedIndexes.map((i) => ({
        value: [pts[i].x, pts[i].y],
        symbolOffset: offsetOf(i),
        // 光环描边用主/客真实配色,其它被点进来对比的队用 --ink(两个主题都 ≥3:1)
        itemStyle: { color: c.surface, borderColor: colorOf(sideOf(pts[i])) === c.grey ? c.ink : colorOf(sideOf(pts[i])), borderWidth: 2 },
      })),
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
      position: "top",
      distance: 6,
      // 只给选中的队标名字(默认主客两队),其余球队靠队徽识别;无队徽的队优先带名,
      // 但会压住旁边队徽的标签已被 labelVisible 提前摘掉(见上面的计算)。
      formatter: (p: unknown) => {
        const d = (p as { data: { pt: StylePoint; side: string | null }; dataIndex: number }).data;
        const idx = (p as { dataIndex: number }).dataIndex;
        if (!labelVisible[idx]) return "";
        if (isSelected(idx)) return `{${d.side ?? "o"}|${d.pt.name}}`;
        return d.pt.crest_url ? "" : `{o|${d.pt.name}}`;
      },
      rich: {
        home: { color: c.teal, fontSize: 11.5, fontWeight: 700 },
        away: { color: c.navy, fontSize: 11.5, fontWeight: 700 },
        o: {
          color: c.ink2,
          fontSize: 11,
          backgroundColor: c.surface,
          padding: [2, 3],
          borderRadius: 2,
        },
      },
    },
    labelLayout: (p) => ({ moveOverlap: "shiftY" as const, hideOverlap: !isSelected(p.dataIndex ?? -1) }),
    markLine: {
      silent: true,
      symbol: "none",
      lineStyle: { color: c.ink3, type: "dashed", opacity: 0.5 },
      label: { show: false },
      data: [{ xAxis: mx }, { yAxis: my }],
    },
  });

  const axisRange = (r: AxisRange | undefined, gap: string) =>
    r
      ? { min: r.min, max: r.max, interval: r.interval }
      : { scale: true, boundaryGap: [gap, gap] as [string, string] };

  return {
    grid,
    xAxis: {
      type: "value",
      name: view.x_label,
      nameLocation: "middle",
      nameGap: 24,
      nameTextStyle: { color: c.ink2, fontSize: 11 },
      ...axisRange(opts.xr, "14%"),
      axisLabel: { color: c.ink2, fontSize: 11 },
      splitLine: { lineStyle: { opacity: 0.1 } },
    },
    yAxis: {
      type: "value",
      name: view.y_label,
      nameLocation: "end",
      nameGap: 12,
      nameTextStyle: { color: c.ink2, fontSize: 11, align: "left" },
      ...axisRange(opts.yr, "16%"),
      axisLabel: { color: c.ink2, fontSize: 11 },
      splitLine: { lineStyle: { opacity: 0.1 } },
    },
    tooltip: {
      confine: true,
      trigger: "item",
      triggerOn: "mousemove",
      formatter: (p: unknown) => {
        const d = (p as { data?: { pt?: PlottedPoint } }).data?.pt;
        if (!d) return "";
        return (
          `<b>${d.name}</b>(${quadOf(d)})<br/>` +
          `${view.x_label} ${fmt(d.x)}(均值 ${fmt(mx)})<br/>` +
          `${view.y_label} ${fmt(d.y)}(均值 ${fmt(my)})`
        );
      },
    },
    series,
  };
}

/** 视角内两根轴的联赛排名(分母 = 该视角有值的球队;y 轴按方向语义决定升降序) */
function rankIn(pts: PlottedPoint[], p: PlottedPoint, axis: "x" | "y", lowerIsBetter: boolean) {
  return competitionRank(
    pts.map((q) => q[axis]),
    p[axis],
    axis === "y" ? lowerIsBetter : false,
  );
}

/** 宽度变化小于这个像素数不重算布局 */
const WIDTH_HYSTERESIS = 8;
const CHART_HEIGHT_DEFAULT = 320;
/** 窄屏加高到这个值——绘图区纵向空间增加,配合 compactOthers 把非本场球队
 * 降级为小圆点,人均可用空间从实测 2916px² 明显改善。 */
const CHART_HEIGHT_NARROW = 380;
/** 宽度低于此值视为"窄屏"——与 compactOthers 共用同一条判断线,不需要
 * 两条不同的断点各自判断产生不一致。 */
const NARROW_WIDTH = 400;

function chartHeightFor(width: number | null): number {
  return width != null && width < NARROW_WIDTH ? CHART_HEIGHT_NARROW : CHART_HEIGHT_DEFAULT;
}

export function TeamStyleQuadrant({
  views,
  leagueTeamStats,
  homeTeamId,
  awayTeamId,
  homeName,
  awayName,
  homeTeamColor,
  awayTeamColor,
  homeTeamBrandColor,
  awayTeamBrandColor,
  windowNote,
  crossLeague = false,
}: {
  /** team_style_preview.py 现只产出「控球×快攻」「传中×禁区触球」两个视角
   *  (近 N 场滚动);第三个「攻守 xG」已改为下面 leagueTeamStats 驱动。 */
  views: StyleView[];
  /** 2026-09-28 新增:球队页 /api/v1/leagues/{id}/team-stats 的整赛季行,
   *  为空(联赛缺 xg 档,或取数失败)时「攻守 xG」标签自动隐藏并说明原因,
   *  不静默降级成空白 tab。 */
  leagueTeamStats: TeamSeasonStatRow[] | null;
  homeTeamId: number;
  awayTeamId: number;
  homeName: string;
  awayName: string;
  /** 2026-08-24:真实球队配色,缺失或对比度不达标时回退品牌青绿/蓝。 */
  homeTeamColor?: TeamColorPair | null;
  awayTeamColor?: TeamColorPair | null;
  /** 该队近期代表色:本场配色缺失或校验不过时的第二级(见 charts/matchTeamColors.ts) */
  homeTeamBrandColor?: TeamBrandColor | null;
  awayTeamBrandColor?: TeamBrandColor | null;
  /** 「近 5 场 · 2026-05-10 至 2026-08-09」——必须带真实日期区间(CLAUDE.md 措辞纪律)。
   *  只用于 legacy 的两个近 N 场视角;「攻守 xG」是整赛季口径,窗口文案由组件自己算。 */
  windowNote: string;
  /** 欧战等跨联赛赛事:本图画的是"该联赛全部球队",两队分处不同联赛时这个
   * 概念本身不成立,不是数据没采够。只影响空态文案,不改绘图逻辑。 */
  crossLeague?: boolean;
}) {
  // 「攻守 xG」:用球队页同一个 both-ends 视角 + plotSet 取点(整赛季,不滚动
  // 近 N 场)——与球队数据页「攻守 xG」tab 逐字节同一套数据函数和组件
  // (quadrantOption.ts::buildQuadrantOption),不是又写一份近似实现。
  const realPts = useMemo(
    () => (leagueTeamStats ? plotSet(leagueTeamStats, REAL_VIEW).pts : []),
    [leagueTeamStats],
  );
  const normalizedViews = useMemo<NormalizedView[]>(() => {
    // 2026-09-28:后端(team_style_preview.py)已不再产出 xg-for-against,
    // style_views 现在恒定只有 poss-fastbreak/cross-box 两个legacy条目,
    // 不需要再对 id 冲突做防御性过滤——2026-09-28 命名审计
    // (scripts/audit/quadrant_audit.py)已在当前部署的 commit 上确认过
    // 0 条同名/子串冲突,这段防御代码是多余的,一并删除。
    const legacy: NormalizedView[] = views.map((v) => ({
      id: v.id,
      tab: v.tab,
      title: v.title,
      x_label: v.x_label,
      y_label: v.y_label,
      digits: v.digits,
      quadrants: v.quadrants as [string, string, string, string],
      y_lower_is_better: v.y_lower_is_better === true,
      points: v.points.filter((p): p is PlottedPoint => p.x != null && p.y != null),
      isReal: false,
      windowLabel: windowNote,
      window: v.window,
    }));
    const real: NormalizedView = {
      id: REAL_VIEW_ID,
      tab: REAL_VIEW.tab,
      title: REAL_VIEW.title,
      x_label: REAL_VIEW.x.label,
      y_label: REAL_VIEW.y.label,
      digits: Math.max(REAL_VIEW.x.digits, REAL_VIEW.y.digits),
      quadrants: REAL_VIEW.quadrants,
      y_lower_is_better: dirsOf(REAL_VIEW).y === true,
      points: realPts.map((p) => ({
        team_id: p.teamId ?? -1,
        name: p.name,
        crest_url: p.crestUrl,
        x: p.x,
        y: p.y,
      })),
      isReal: true,
      realPts,
      windowLabel: "本联赛本赛季（与球队数据页「攻守 xG」口径一致）",
      window: null,
    };
    // 沿用原来的 tab 顺序:控球×快攻/传中×禁区触球 在前,攻守 xG 放最后
    // (与旧版 _TEAM_STAT_VIEWS 的顺序一致,不打乱既有视觉习惯)。
    return [...legacy, real];
  }, [views, realPts, windowNote]);

  const available = useMemo(() => normalizedViews.filter(usable), [normalizedViews]);
  const [viewId, setViewId] = useState<string | null>(null);
  const view = available.find((v) => v.id === viewId) ?? available[0];
  const defaultPair = useMemo(() => [homeTeamId, awayTeamId], [homeTeamId, awayTeamId]);
  const [selectedIds, setSelectedIds] = useState<number[]>(defaultPair);

  const boxRef = useRef<HTMLDivElement>(null);
  const consumedRef = useRef(false);
  const [width, setWidth] = useState<number | null>(null);
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      if (w <= 0) return;
      setWidth((prev) => (prev != null && Math.abs(prev - w) < WIDTH_HYSTERESIS ? prev : w));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const isDefault =
    selectedIds.length === 2 && selectedIds.every((id) => defaultPair.includes(id));
  useEffect(() => {
    if (isDefault) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelectedIds(defaultPair);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isDefault, defaultPair]);

  const { c, resolved, effectiveColors } = useMatchColors(
    { homeTeamColor, awayTeamColor, homeTeamBrandColor, awayTeamBrandColor },
    "surface",
  );

  const pts = useMemo(
    () =>
      view
        ? view.points.filter((p): p is PlottedPoint => p.x != null && p.y != null)
        : [],
    [view],
  );

  const derived = useMemo(() => {
    if (!view || pts.length < 4) return null;
    const mx = mean(pts.map((p) => p.x));
    const my = mean(pts.map((p) => p.y));
    const xr = niceAxisRange(pts.map((p) => p.x), { pad: 0.14 });
    const yr = niceAxisRange(pts.map((p) => p.y), { pad: 0.16 });
    const box: PlotBox | null = width ? { width, height: chartHeightFor(width), grid: QUADRANT_GRID } : null;
    const crestSize = box ? crestSizeFor(box, pts.length) : CREST.MIN + 4;
    const layout = box
      ? layoutCrests({ pts, box, xr, yr, yInverse: false, radius: crestSize / 2 + CREST.PAD })
      : null;
    return { mx, my, xr, yr, crestSize, layout };
  }, [view, pts, width]);

  // compactOthers 与 box 用同一条 NARROW_WIDTH 判断线,窄屏才降级非本场球队。
  const isNarrow = width != null && width < NARROW_WIDTH;

  const selected = useMemo(
    () => selectedIds.map((id) => pts.find((p) => p.team_id === id)).filter((p): p is PlottedPoint => !!p),
    [selectedIds, pts],
  );

  const option = useMemo(() => {
    if (!view || !derived) return null;
    const { mx, my, xr, yr, crestSize, layout } = derived;
    if (view.isReal) {
      // 「攻守 xG」:复用球队页的 option 构造器(quadrantOption.ts),不是
      // 再拿本文件的 buildOption 凑一份近似实现——两边渲染逻辑(队徽避让、
      // 四象限角标、tooltip 措辞)完全一致,以后只用改一处。
      const realPtsList = view.realPts ?? [];
      const selectedIndexes = realPtsList
        .map((p, i) => (p.teamId != null && selectedIds.includes(p.teamId) ? i : -1))
        .filter((i) => i >= 0);
      const labelled = new Set([
        ...outlierNames(realPtsList, mx, my),
        ...realPtsList.filter((p) => !p.crestUrl).map((p) => p.name),
        ...selectedIndexes.map((i) => realPtsList[i].name),
      ]);
      return buildQuadrantOption({
        view: REAL_VIEW,
        pts: realPtsList,
        mx,
        my,
        colors: effectiveColors,
        labelled,
        crestSize,
        layout,
        xr,
        yr,
        grid: QUADRANT_GRID,
        selectedIndexes,
      });
    }
    return buildOption(view, pts, mx, my, homeTeamId, awayTeamId, effectiveColors, {
      crestSize,
      layout,
      xr,
      yr,
      grid: QUADRANT_GRID,
      selectedIds,
      compactOthers: isNarrow,
    });
  }, [view, derived, pts, homeTeamId, awayTeamId, effectiveColors, selectedIds, isNarrow]);

  if (!view || !derived || !option) {
    return (
      <section className={pageStyles.section}>
        <h2 className={pageStyles.sectionTitle}>
          <span className={pageStyles.sectionBar} aria-hidden />
          球队风格定位
        </h2>
        <p className={pageStyles.emptyText}>
          {crossLeague
            ? "本图把同一联赛的球队放在同一把尺子上比较。欧战这类跨联赛赛事的参赛队来自不同联赛,没有共同的尺子,因此不绘制象限图——不是数据没采够。"
            : "该联赛该赛季的球队指标不足以绘制象限图(有效球队少于 4 支)。"}
        </p>
      </section>
    );
  }

  const { mx, my } = derived;
  const fmt = (v: number) => v.toFixed(view.digits);
  const quadOf = (p: { x: number; y: number }) => view.quadrants[quadOfFor(view, p, mx, my)];
  const lowerY = view.y_lower_is_better === true;

  // ECharts 的 click 只在点到图形元素时触发,点空白 canvas 没有回调——
  // "点空白回到本场两队"靠外层 div 的 DOM onClick,用 ref 记一下这次点击是否已被图上元素消费。
  const handleChartClick = (params: unknown) => {
    const p = params as { seriesName?: string; dataIndex?: number; data?: { pt?: StylePoint } } | null;
    const hit =
      p && (p.seriesName === "crest" || p.seriesName === "hit") && typeof p.dataIndex === "number"
        ? pts[p.dataIndex]
        : p?.data?.pt;
    if (!hit) return;
    consumedRef.current = true;
    setSelectedIds((prev) => {
      if (prev.includes(hit.team_id)) return prev.filter((id) => id !== hit.team_id);
      const next = [...prev, hit.team_id];
      return next.length > 2 ? next.slice(next.length - 2) : next;
    });
  };

  const home = pts.find((p) => p.team_id === homeTeamId);
  const away = pts.find((p) => p.team_id === awayTeamId);
  const ariaSummary =
    `${view.title}象限图,共 ${pts.length} 支球队。` +
    `虚线是这 ${pts.length} 支球队${view.windowLabel}的平均值(${view.x_label} ${fmt(mx)},${view.y_label} ${fmt(my)}),` +
    `只在联赛内部比较,不能跨联赛。` +
    (home ? `${homeName} ${fmt(home.x)} / ${fmt(home.y)},落在「${quadOf(home)}」。` : `${homeName} 缺该视角数据。`) +
    (away ? `${awayName} ${fmt(away.x)} / ${fmt(away.y)},落在「${quadOf(away)}」。` : `${awayName} 缺该视角数据。`) +
    `描述的是${view.windowLabel}怎么踢,不是对本场的预测。` +
    (!isDefault && selected.length
      ? `当前对比:${selected.map((p) => `${p.name}(${quadOf(p)})`).join("、")}。`
      : "");

  const sideColor = (p: StylePoint) =>
    p.team_id === homeTeamId ? resolved.home : p.team_id === awayTeamId ? resolved.away : c.ink;

  return (
    <section className={pageStyles.section}>
      <h2 className={pageStyles.sectionTitle}>
        <span className={pageStyles.sectionBar} aria-hidden />
        球队风格定位
      </h2>

      <div className={styles.viewTabs} role="tablist" aria-label="象限图视角">
        {/* N=0 时(如联赛缺 xg 档、team-stats 取数失败)该视角标签直接不出现,
            不是灰置(disabled) —— 2026-09-28 站长要求"自动隐藏标签",不再让
            用户点到一个永远打不开的按钮。仍旧无法出现在 available 里的
            (可能达标但队数不足 4)才保留旧的灰置 + title 提示。 */}
        {normalizedViews.filter((v) => v.isReal ? leagueTeamStats != null : true).map((v) => {
          const ok = available.some((a) => a.id === v.id);
          return (
            <button
              key={v.id}
              type="button"
              role="tab"
              aria-selected={v.id === view.id}
              disabled={!ok}
              title={ok ? undefined : "该联赛该赛季缺少此视角所需的数据"}
              className={v.id === view.id ? styles.viewTabOn : styles.viewTab}
              onClick={() => setViewId(v.id)}
            >
              {v.tab}
            </button>
          );
        })}
      </div>
      {leagueTeamStats == null && (
        <p className={styles.windowNote}>
          {REAL_VIEW.tab}：该联赛该赛季数据不足或缺少 xG 档，标签已隐藏。
        </p>
      )}

      <div className={styles.chartCard}>
        <div className={styles.chartHead}>
          <strong className={styles.chartTitle}>{view.title}</strong>
        </div>
        {/* 摘要只给读屏(站长 2026-10-02 删去卡片底部的可见摘要段落),不显示 */}
        {/* 键盘路径走 Esc 与「恢复本场两队」,这个 div 的 onClick 只服务鼠标/触屏"点空白" */}
        <div
          ref={boxRef}
          onClick={() => {
            if (consumedRef.current) {
              consumedRef.current = false;
              return;
            }
            setSelectedIds(defaultPair);
          }}
        >
          <EChart
            option={option}
            height={chartHeightFor(width)}
            ariaSummary={ariaSummary}
            showSummary={false}
            onEvents={{ click: handleChartClick }}
          />
        </div>

        <div className={panelStyles.panel} aria-live="polite" data-empty={selected.length ? undefined : "true"}>
          {selected.length === 0 ? (
            <p className={panelStyles.placeholder}>点击图上的队徽查看该队在联赛里的位置。</p>
          ) : (
            <>
              <div className={panelStyles.head}>
                <div className={panelStyles.teams}>
                  {selected.map((p) => (
                    <div key={p.team_id} className={panelStyles.team}>
                      <TeamBadge teamName={p.name} crestUrl={p.crest_url} size={28} />
                      <span className={panelStyles.teamName} style={{ color: sideColor(p) }}>
                        {p.name}
                      </span>
                      <span className={panelStyles.quad}>{quadOf(p)}</span>
                    </div>
                  ))}
                </div>
                {!isDefault && (
                  <button
                    type="button"
                    className={panelStyles.clear}
                    onClick={() => setSelectedIds(defaultPair)}
                  >
                    恢复本场两队
                  </button>
                )}
              </div>
              <div className={panelStyles.tableWrap}>
                <table className={panelStyles.table}>
                  <thead>
                    <tr>
                      <th scope="col">{view.title}</th>
                      {selected.map((p) => (
                        <th key={p.team_id} scope="col">
                          {p.name}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {(["x", "y"] as const).map((axis) => (
                      <tr key={axis}>
                        <td>{axis === "x" ? view.x_label : view.y_label}</td>
                        {selected.map((p) => {
                          const r = rankIn(pts, p, axis, lowerY);
                          const m = axis === "x" ? mx : my;
                          const delta = p[axis] - m;
                          const good = axis === "y" && lowerY ? delta < 0 : delta > 0;
                          return (
                            <td key={p.team_id} data-label={p.name}>
                              <span className={panelStyles.value}>{fmt(p[axis])}</span>
                              <span className={panelStyles.rank}>
                                第 {r.rank}/{r.total}
                              </span>
                              <span
                                className={`${panelStyles.delta} ${
                                  delta === 0 ? "" : good ? panelStyles.deltaGood : panelStyles.deltaBad
                                }`}
                              >
                                均值 {fmt(m)}（{delta > 0 ? "+" : ""}
                                {fmt(delta)}）
                              </span>
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>

        <div className={styles.legendRow}>
          <span className={styles.legendItem}>
            <span className={styles.swatchDot} style={{ background: resolved.home }} />
            {homeName}
          </span>
          <span className={styles.legendItem}>
            <span className={styles.swatchDot} style={{ background: resolved.away }} />
            {awayName}
          </span>
          <span className={styles.legendItem}>
            <span className={styles.swatchDot} style={{ background: c.grey }} />
            联赛其余球队（队徽半透明）
          </span>
        </div>
      </div>
    </section>
  );
}
