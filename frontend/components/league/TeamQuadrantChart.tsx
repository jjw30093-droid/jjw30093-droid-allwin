"use client";

/**
 * 球队象限图 —— 用联赛平均线把散点切成四象限,坐标点直接用真实队徽渲染,
 * 20 支球队一眼全认得出;点击队徽(或下方名单)选中,其余变暗,下方面板给出
 * 该队在当前视角的数值与联赛内排名,最多选两队并排对比。
 *
 * 2026-09-09 改造。视角定义与纯逻辑在 quadrantViews.ts,option 构造在
 * quadrantOption.ts(可 headless 冒烟),布局数学在 charts/crestQuadrantLayout.ts,
 * 面板在 TeamQuadrantDetail.tsx——本文件只剩状态与接线。
 *
 * 选中态是字符串 key(不是对象引用):ECharts 在 setOption 里克隆 data[i]
 * 的嵌套对象,params.data.pt 与原始 Pt 引用不相等,靠引用判断会永远落空。
 * 陈旧选中由 resolveSelectedTeams 作为派生态自动挡掉(切到该队缺数据的视角时
 * 面板落回占位态,切回来又恢复),不用 useEffect 清空。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { EChart } from "@/components/EChart";
import { useChartColors } from "@/components/charts/useChartColors";
import {
  CREST,
  QUADRANT_GRID,
  crestSizeFor,
  layoutCrests,
  niceAxisRange,
  type PlotBox,
} from "@/components/charts/crestQuadrantLayout";
import type { TeamSeasonStatRow } from "@/lib/api-v1";
import { buildQuadrantOption } from "./quadrantOption";
import {
  VIEWS,
  collectPoints,
  fmt,
  mean,
  outlierNames,
  quadrantOf,
  resolveClickedKey,
  resolveSelectedTeams,
  toggleSelection,
  type Pt,
} from "./quadrantViews";
import { TeamQuadrantDetail } from "./TeamQuadrantDetail";
import styles from "./TeamQuadrantChart.module.css";

export {
  collectPoints,
  outlierNames,
  quadrantOf,
  type Pt,
  type __TestView,
} from "./quadrantViews";

/** 宽度变化小于这个像素数不重算布局,避免拖动窗口时反复重排 */
const WIDTH_HYSTERESIS = 8;

function chartHeightFor(n: number) {
  // 球队越多越需要纵向空间给队徽错开,20 队 → 380
  return Math.max(340, n * 19);
}

export function TeamQuadrantChart({ rows }: { rows: TeamSeasonStatRow[] }) {
  const c = useChartColors();
  // 攻防视角依赖数据源的 xg 档,不是每个联赛赛季都有 —— 先算可用性再定默认视角
  const available = useMemo(
    () => VIEWS.filter((v) => collectPoints(rows, v).length >= 4),
    [rows],
  );
  const [viewId, setViewId] = useState<string | null>(null);
  const view = available.find((v) => v.id === viewId) ?? available[0];
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);

  // 图表宽度:自己测(ShotMapChart 的既有范式),不依赖 EChart 暴露实例
  const boxRef = useRef<HTMLDivElement>(null);
  // ECharts 的 click 只在点到图形元素时触发,点空白 canvas 不会有回调——
  // 所以"点空白清空"靠外层 div 的 DOM onClick:zrender 绑在 canvas 上的原生
  // 监听先于外层 React 事件跑,用 ref 记一下"这次点击已被图上元素消费"。
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

  // Escape 清空选中:只在有选中时挂监听,不 stopPropagation
  useEffect(() => {
    if (!selectedKeys.length) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelectedKeys([]);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedKeys.length]);

  const pts = useMemo(() => (view ? collectPoints(rows, view) : []), [rows, view]);

  const derived = useMemo(() => {
    if (!view || pts.length < 4) return null;
    const mx = mean(pts.map((p) => p.x));
    const my = mean(pts.map((p) => p.y));
    const lowY = view.y.lowerIsBetter === true;
    const xr = niceAxisRange(pts.map((p) => p.x), { pad: 0.14 });
    const yr = niceAxisRange(pts.map((p) => p.y), { pad: 0.16 });
    const height = chartHeightFor(pts.length);
    const box: PlotBox | null = width ? { width, height, grid: QUADRANT_GRID } : null;
    const crestSize = box ? crestSizeFor(box, pts.length) : CREST.MIN + 6;
    const layout = box
      ? layoutCrests({ pts, box, xr, yr, yInverse: lowY, radius: crestSize / 2 + CREST.PAD })
      : null;
    return { mx, my, lowY, xr, yr, height, crestSize, layout };
  }, [view, pts, width]);

  const selected = useMemo(() => resolveSelectedTeams(pts, selectedKeys), [pts, selectedKeys]);

  const option = useMemo(() => {
    if (!view || !derived) return null;
    const { mx, my, xr, yr, crestSize, layout } = derived;
    // 窄屏队名标签更容易压在相邻队徽上,只标最极端的 4 支;宽屏 6 支
    const labelled = outlierNames(pts, mx, my, width != null && width < 480 ? 4 : 6);
    for (const p of pts) if (!p.crestUrl) labelled.add(p.name);
    for (const p of selected) labelled.add(p.name);
    return buildQuadrantOption({
      view,
      pts,
      mx,
      my,
      colors: c,
      labelled,
      crestSize,
      layout,
      xr,
      yr,
      grid: QUADRANT_GRID,
      selectedIndexes: selected.map((s) => pts.indexOf(s)),
    });
  }, [view, derived, pts, selected, c, width]);

  if (!view || !derived || !option) {
    return (
      <p className={styles.empty}>
        该联赛该赛季的球队指标不足以绘制象限图（有效球队少于 4 支）。
      </p>
    );
  }

  const { mx, my, lowY, height } = derived;
  const quad = (p: Pt) => quadrantOf(p, mx, my, lowY);

  const handleChartClick = (params: unknown) => {
    const key = resolveClickedKey(params, pts);
    if (key == null) return;
    consumedRef.current = true;
    // 点已选中的 = 取消;其余 = 追加(满 2 时 FIFO)
    setSelectedKeys((prev) => toggleSelection(prev, key));
  };
  const handleBoxClick = () => {
    if (consumedRef.current) {
      consumedRef.current = false;
      return;
    }
    setSelectedKeys([]);
  };
  const toggle = (key: string) => setSelectedKeys((prev) => toggleSelection(prev, key));

  const sampleNote = (() => {
    const mps = pts.map((p) => p.mp).filter((m): m is number => m != null);
    if (!mps.length) return "";
    const lo = Math.min(...mps);
    const hi = Math.max(...mps);
    return lo === hi ? `每队 ${lo} 场` : `每队 ${lo}–${hi} 场`;
  })();

  const byQuadrant = view.quadrants.map((label, i) => ({
    label,
    teams: pts.filter((p) => quad(p) === i),
  }));

  // 摘要措辞与改造前逐字一致(e2e 依赖),有选中时只在末尾追加一句
  const ariaSummary =
    `${view.title}象限图，虚线是本联赛本赛季平均值（${view.x.label} ${fmt(mx, view.x)}，` +
    `${view.y.label} ${fmt(my, view.y)}）。` +
    byQuadrant
      .filter((q) => q.teams.length)
      .map((q) => `${q.label}：${q.teams.map((p) => p.name).join("、")}`)
      .join("；") +
    `。共 ${pts.length} 支球队${sampleNote ? `，${sampleNote}` : ""}。` +
    `参考线是联赛内部平均，不能拿来跨联赛比较。` +
    (selected.length
      ? `当前选中：${selected.map((p) => `${p.name}（${view.quadrants[quad(p)]}）`).join("、")}。`
      : "");

  return (
    <section className={styles.card}>
      <header className={styles.head}>
        <div>
          <h2 className={styles.title}>球队象限图</h2>
          <p className={styles.sub}>
            {view.title} · 虚线为本联赛本赛季平均
            {sampleNote ? ` · ${sampleNote}` : ""}
          </p>
        </div>
        <div className={styles.tabs} role="tablist" aria-label="象限图视角">
          {VIEWS.map((v) => {
            const usable = available.some((a) => a.id === v.id);
            return (
              <button
                key={v.id}
                type="button"
                role="tab"
                aria-selected={v.id === view.id}
                disabled={!usable}
                title={usable ? undefined : "该联赛该赛季缺少此视角所需的数据"}
                className={v.id === view.id ? styles.tabOn : styles.tab}
                onClick={() => setViewId(v.id)}
              >
                {v.tab}
              </button>
            );
          })}
        </div>
      </header>

      {/* chartBox 不能有 padding/border:它的内容宽度必须等于图表宽度,布局才能对上像素 */}
      {/* 键盘路径走名单按钮与 Esc,这个 div 的 onClick 只服务鼠标/触屏"点空白"。
          data-crest-layout 只在非生产环境输出:e2e/Playwright 需要知道每个队徽
          实际画在哪(真实坐标 + 避让偏移)才能点得中它。 */}
      <div
        ref={boxRef}
        className={styles.chartBox}
        onClick={handleBoxClick}
        data-crest-layout={
          process.env.NODE_ENV !== "production" && derived.layout
            ? JSON.stringify(
                pts.map((p, i) => [
                  p.key,
                  Math.round(derived.layout!.base[i].px + derived.layout!.offset[i][0]),
                  Math.round(derived.layout!.base[i].py + derived.layout!.offset[i][1]),
                ]),
              )
            : undefined
        }
      >
        <EChart
          option={option}
          height={height}
          ariaSummary={ariaSummary}
          onEvents={{ click: handleChartClick }}
        />
      </div>

      <TeamQuadrantDetail
        view={view}
        rows={rows}
        selected={selected}
        mx={mx}
        my={my}
        onRemove={(key) => setSelectedKeys((prev) => prev.filter((k) => k !== key))}
        onClear={() => setSelectedKeys([])}
      />

      <div className={styles.legend}>
        {byQuadrant
          .filter((q) => q.teams.length)
          .map((q) => (
            <div key={q.label} className={styles.legendRow}>
              <span className={styles.legendKey}>{q.label}</span>
              <span className={styles.legendVal}>
                {q.teams.map((p) => (
                  <button
                    key={p.key}
                    type="button"
                    className={
                      selectedKeys.includes(p.key) ? styles.legendTeamOn : styles.legendTeam
                    }
                    aria-pressed={selectedKeys.includes(p.key)}
                    onClick={() => toggle(p.key)}
                  >
                    {p.name}
                  </button>
                ))}
              </span>
            </div>
          ))}
      </div>

      <p className={styles.note}>
        {view.note} 图上每支球队用队徽表示，位置挤在一起的会自动错开一点避免遮挡；
        精确数值以点击后的详情面板为准。点击队徽或上方名单查看该队数值与排名，
        再点一支可对比，按 Esc 或点空白处取消。
        {available.length < VIEWS.length && (
          <>
            {" "}
            灰掉的视角是该联赛该赛季数据源没有提供对应指标，不是本站算不出来。
          </>
        )}
      </p>
    </section>
  );
}
