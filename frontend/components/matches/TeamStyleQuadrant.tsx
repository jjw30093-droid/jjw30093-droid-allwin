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
import { useChartColors, type ChartColors } from "@/components/charts/useChartColors";
import { resolveMatchColors, type TeamColorPair } from "@/components/charts/matchTeamColors";
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
import styles from "./MatchDataModules.module.css";
import panelStyles from "@/components/league/TeamQuadrantDetail.module.css";
import pageStyles from "@/app/matches/[matchId]/match-detail.module.css";
import type { components } from "@/lib/api-types";

type StyleView = components["schemas"]["MatchPreviewStyleViewDTO"];
type StylePoint = components["schemas"]["MatchPreviewStylePointDTO"];
type PlottedPoint = StylePoint & { x: number; y: number };

function usable(v: StyleView) {
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
};

/** 2026-08-24 抽出为可独立渲染冒烟测试的纯函数(CLAUDE.md §11.3)。 */
export function buildOption(
  view: StyleView,
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

  const crestData = pts.map((p, i) => {
    const side = sideOf(p);
    const sel = isSelected(i);
    const opacity = sel ? 1 : CREST.DIM_OPACITY;
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
          const want = sel || !p.crest_url;
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
const CHART_HEIGHT = 320;

export function TeamStyleQuadrant({
  views,
  homeTeamId,
  awayTeamId,
  homeName,
  awayName,
  homeTeamColor,
  awayTeamColor,
  windowNote,
}: {
  views: StyleView[];
  homeTeamId: number;
  awayTeamId: number;
  homeName: string;
  awayName: string;
  /** 2026-08-24:真实球队配色,缺失或对比度不达标时回退品牌青绿/蓝。 */
  homeTeamColor?: TeamColorPair | null;
  awayTeamColor?: TeamColorPair | null;
  /** 「近 5 场 · 2026-05-10 至 2026-08-09」——必须带真实日期区间(CLAUDE.md 措辞纪律) */
  windowNote: string;
}) {
  const available = useMemo(() => views.filter(usable), [views]);
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

  const c = useChartColors();
  const resolved = useMemo(
    () =>
      resolveMatchColors(homeTeamColor, awayTeamColor, {
        isDark: c.isDark,
        backgroundHex: c.surface,
        fallback: { home: c.teal, away: c.navy },
      }),
    [homeTeamColor, awayTeamColor, c],
  );
  const effectiveColors: ChartColors = useMemo(
    () => ({ ...c, teal: resolved.home, navy: resolved.away }),
    [c, resolved],
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
    const box: PlotBox | null = width ? { width, height: CHART_HEIGHT, grid: QUADRANT_GRID } : null;
    const crestSize = box ? crestSizeFor(box, pts.length) : CREST.MIN + 4;
    const layout = box
      ? layoutCrests({ pts, box, xr, yr, yInverse: false, radius: crestSize / 2 + CREST.PAD })
      : null;
    return { mx, my, xr, yr, crestSize, layout };
  }, [view, pts, width]);

  const selected = useMemo(
    () => selectedIds.map((id) => pts.find((p) => p.team_id === id)).filter((p): p is PlottedPoint => !!p),
    [selectedIds, pts],
  );

  const option = useMemo(() => {
    if (!view || !derived) return null;
    const { mx, my, xr, yr, crestSize, layout } = derived;
    return buildOption(view, pts, mx, my, homeTeamId, awayTeamId, effectiveColors, {
      crestSize,
      layout,
      xr,
      yr,
      grid: QUADRANT_GRID,
      selectedIds,
    });
  }, [view, derived, pts, homeTeamId, awayTeamId, effectiveColors, selectedIds]);

  if (!view || !derived || !option) {
    return (
      <section className={pageStyles.section}>
        <h2 className={pageStyles.sectionTitle}>
          <span className={pageStyles.sectionBar} aria-hidden />
          球队风格定位
        </h2>
        <p className={pageStyles.emptyText}>
          该联赛该赛季的球队指标不足以绘制象限图(有效球队少于 4 支)。
        </p>
      </section>
    );
  }

  const { mx, my } = derived;
  const fmt = (v: number) => v.toFixed(view.digits);
  const quadOf = (p: { x: number; y: number }) => view.quadrants[quadrantIndex(p, mx, my)];
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
    `虚线是这 ${pts.length} 支球队${windowNote}的平均值(${view.x_label} ${fmt(mx)},${view.y_label} ${fmt(my)}),` +
    `只在联赛内部比较,不能跨联赛。` +
    (home ? `${homeName} ${fmt(home.x)} / ${fmt(home.y)},落在「${quadOf(home)}」。` : `${homeName} 缺该视角数据。`) +
    (away ? `${awayName} ${fmt(away.x)} / ${fmt(away.y)},落在「${quadOf(away)}」。` : `${awayName} 缺该视角数据。`) +
    `描述的是${windowNote}怎么踢,不是对本场的预测。` +
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
      <p className={styles.windowNote}>{windowNote}</p>

      <div className={styles.viewTabs} role="tablist" aria-label="象限图视角">
        {views.map((v) => {
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

      <div className={styles.chartCard}>
        <div className={styles.chartHead}>
          <strong className={styles.chartTitle}>{view.title}</strong>
          <span className={styles.chartSample}>{pts.length} 支 · 每队 5 场</span>
        </div>
        {/* 卡片自带摘要段落,关掉 EChart 内置摘要避免重复(a11y label 仍在) */}
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
            height={CHART_HEIGHT}
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
                            <td key={p.team_id}>
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
              <p className={panelStyles.foot}>
                「第 N/M」= 联赛内排名 / 该视角有数据的球队数，为{windowNote}联赛内部比较；
                {lowerY ? `${view.y_label}越低越好，排名按升序；` : ""}
                点击其它队徽可换成与它对比，按 Esc 或点空白处回到本场两队。
              </p>
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
        <p className={styles.summary}>{ariaSummary}</p>
      </div>
    </section>
  );
}
