"use client";

/**
 * 联赛球员象限图(2026-09-15)。对标 TeamQuadrantChart.tsx——用联赛平均线把
 * 散点切成四象限,坐标点用球员头像渲染,点击选中、最多两人对比,下方面板
 * 给出该球员在当前视角的数值与位置内排名。
 *
 * 与球队版的三处关键差异:
 * 1. **位置选择器是比较基准,不是纯过滤器**——均值/象限归属/排名全部在
 *    "当前选中位置"内部重算(见 playerQuadrantViews.ts 头注释,贝林厄姆
 *    案例)。位置切换用本地 state,不经过 URL/服务端往返——服务端已经
 *    一次性把全部位置的数据都下发了。
 * 2. **每象限只画离均值最远的 10 人**(站长拍板)——playerPlotSet 算出
 *    全部达标球员后,topPerQuadrant 再从中按象限各挑 10 个来画;均值/
 *    虚线仍由全部达标球员计算,不受这条截断影响,图下有独立文案说明。
 * 3. 没有"最近 N 场/主客场"筛选(球队侧刚做完,球员侧明确不在本次范围)。
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
import type { PlayerQuadrantRow } from "@/lib/api-v1";
import { buildQuadrantOption } from "./quadrantOption";
import {
  PLAYER_VIEWS,
  dirsOf,
  fmt,
  mean,
  playerHiddenNote,
  playerPlotSet,
  quadrantOf,
  quadrantTruncationNote,
  resolveClickedKey,
  resolveSelectedPlayers,
  toggleSelection,
  topPerQuadrant,
  type PlayerPt,
} from "./playerQuadrantViews";
import { POSITIONS, type PositionValue } from "./playerMetrics";
import { PlayerQuadrantDetail } from "./PlayerQuadrantDetail";
// 复用球队象限图的卡片外壳样式(.card/.groups/.tabs/.legend 等类名在
// 那个模块里已经是中性命名,不含"球队"专属语义,两个组件共用同一份
// .module.css 是本代码库既有先例——本会话早些时候 SeasonSwitcher.module.css
// 就被 3 个不同组件同时 import)。
import styles from "./TeamQuadrantChart.module.css";

/** 球员头像比队徽密一倍(4 象限 × 10 人 = 最多 40 个点 vs 球队版 20 个),
 *  FILL_TARGET/MIN/MAX 都要相应调小,否则会掉到 CREST.MIN 糊成一团。 */
const PLAYER_CREST_OPTS = { fillTarget: 0.28, min: 20, max: 30 };

const WIDTH_HYSTERESIS = 8;

function chartHeightFor(n: number) {
  return Math.max(340, n * 19);
}

export function PlayerQuadrantChart({ rows }: { rows: PlayerQuadrantRow[] }) {
  const c = useChartColors();
  const [position, setPosition] = useState<PositionValue>(2);
  const [viewId, setViewId] = useState<string | null>(null);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);

  const positionRows = useMemo(
    () => rows.filter((r) => r.usual_position === position),
    [rows, position],
  );

  // 每个视角各自算一次全量点集与隐藏名单——均值/达标判定基于"当前选中位置
  // 内的全部达标球员",不是画出来的那 10 个(见文件头注释)。
  const fullPlots = useMemo(
    () => new Map(PLAYER_VIEWS.map((v) => [v.id, playerPlotSet(positionRows, v)] as const)),
    [positionRows],
  );
  const available = useMemo(
    () => PLAYER_VIEWS.filter((v) => (fullPlots.get(v.id)?.pts.length ?? 0) >= 4),
    [fullPlots],
  );
  const view = available.find((v) => v.id === viewId) ?? available[0];

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

  useEffect(() => {
    if (!selectedKeys.length) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelectedKeys([]);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedKeys.length]);

  const { fullPts, hidden } = useMemo(() => {
    const p = view ? fullPlots.get(view.id) : null;
    return { fullPts: p?.pts ?? [], hidden: p?.hidden ?? [] };
  }, [fullPlots, view]);

  const derived = useMemo(() => {
    if (!view || fullPts.length < 4) return null;
    const mx = mean(fullPts.map((p) => p.x));
    const my = mean(fullPts.map((p) => p.y));
    const dirs = dirsOf(view);
    const lowY = dirs.y === true;
    const { drawn, totalByQuadrant } = topPerQuadrant(fullPts, mx, my, dirs);
    const xr = niceAxisRange(drawn.map((p) => p.x), { pad: 0.14 });
    const yr = niceAxisRange(drawn.map((p) => p.y), { pad: 0.16 });
    const height = chartHeightFor(drawn.length);
    const box: PlotBox | null = width ? { width, height, grid: QUADRANT_GRID } : null;
    const crestSize = box ? crestSizeFor(box, drawn.length, PLAYER_CREST_OPTS) : PLAYER_CREST_OPTS.min + 4;
    const layout = box
      ? layoutCrests({ pts: drawn, box, xr, yr, yInverse: lowY, radius: crestSize / 2 + CREST.PAD })
      : null;
    return { mx, my, dirs, lowY, xr, yr, height, crestSize, layout, drawn, totalByQuadrant };
  }, [view, fullPts, width]);

  const selected = useMemo(
    () => (derived ? resolveSelectedPlayers(derived.drawn, selectedKeys) : []),
    [derived, selectedKeys],
  );

  const option = useMemo(() => {
    if (!view || !derived) return null;
    const { mx, my, xr, yr, crestSize, layout, drawn } = derived;
    const labelled = new Set<string>();
    for (const p of drawn) if (!p.avatarUrl) labelled.add(p.name);
    for (const p of selected) labelled.add(p.name);
    return buildQuadrantOption<PlayerPt>({
      view,
      pts: drawn,
      mx,
      my,
      colors: c,
      labelled,
      crestSize,
      layout,
      xr,
      yr,
      grid: QUADRANT_GRID,
      selectedIndexes: selected.map((s) => drawn.indexOf(s)),
      symbolUrlOf: (p) => p.avatarUrl,
    });
  }, [view, derived, selected, c]);

  if (!view || !derived || !option) {
    return (
      <p className={styles.empty}>
        该联赛该赛季这个位置的球员数据不足以绘制象限图（达标球员少于 4 人）。
      </p>
    );
  }

  const { mx, my, dirs, height, drawn, totalByQuadrant } = derived;
  const quad = (p: PlayerPt) => quadrantOf(p, mx, my, dirs);

  const handleChartClick = (params: unknown) => {
    const key = resolveClickedKey(params, drawn);
    if (key == null) return;
    consumedRef.current = true;
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
    const mps = drawn.map((p) => p.mp).filter((m): m is number => m != null);
    if (!mps.length) return "";
    const lo = Math.min(...mps);
    const hi = Math.max(...mps);
    return lo === hi ? `每人 ${lo} 场` : `每人 ${lo}–${hi} 场`;
  })();

  const outcomeVarianceBanner =
    view.x.semantic === "outcome_variance" || view.y.semantic === "outcome_variance"
      ? "这个视角展示的是本赛季至今的结果记录，不是稳定的能力评价，赛季间相关性低，不代表未来表现。"
      : null;

  const byQuadrant = view.quadrants.map((label, i) => ({
    label,
    players: drawn.filter((p) => quad(p) === i),
  }));

  const hiddenText = playerHiddenNote(hidden);
  const truncationText = quadrantTruncationNote(totalByQuadrant);

  const disabledViews = PLAYER_VIEWS.filter((v) => !available.some((a) => a.id === v.id)).reduce(
    (acc, v) => {
      const p = fullPlots.get(v.id)!;
      const belowThreshold = p.hidden.filter((h) => h.reason === "below_threshold").length;
      if (p.pts.length + belowThreshold >= 4) acc.hasSample = true;
      else acc.hasMissing = true;
      return acc;
    },
    { hasMissing: false, hasSample: false },
  );

  const positionName = POSITIONS.find((p) => p.value === position)?.label ?? "";
  const ariaSummary =
    `${view.title}象限图（${positionName}），虚线是选中位置内的平均值（${view.x.label} ${fmt(mx, view.x)}，` +
    `${view.y.label} ${fmt(my, view.y)}）。` +
    byQuadrant
      .filter((q) => q.players.length)
      .map((q) => `${q.label}：${q.players.map((p) => p.name).join("、")}`)
      .join("；") +
    `。共 ${drawn.length} 名球员${sampleNote ? `，${sampleNote}` : ""}。` +
    (truncationText ? `${truncationText}` : "") +
    (hiddenText ? `${hiddenText}` : "") +
    `参考线只在选中的位置内部比较，不能跨位置或跨联赛比较。` +
    (selected.length
      ? `当前选中：${selected.map((p) => `${p.name}（${view.quadrants[quad(p)]}）`).join("、")}。`
      : "");

  return (
    <section className={styles.card}>
      <header className={styles.head}>
        <div className={styles.headTop}>
          <div>
            <h2 className={styles.title}>球员象限图</h2>
            <p className={styles.sub}>
              {positionName} · {view.title} · 虚线为选中位置内平均
              {sampleNote ? ` · ${sampleNote}` : ""}
            </p>
          </div>
        </div>

        <div className={styles.groups} role="group" aria-label="选择位置">
          {POSITIONS.map((p) => (
            <button
              key={p.value}
              type="button"
              aria-pressed={p.value === position}
              className={p.value === position ? styles.groupOn : styles.group}
              onClick={() => setPosition(p.value)}
            >
              {p.label}
            </button>
          ))}
        </div>

        <div className={styles.tabs} role="tablist" aria-label="球员象限图视角">
          {PLAYER_VIEWS.map((v) => {
            const usable = available.some((a) => a.id === v.id);
            return (
              <button
                key={v.id}
                type="button"
                role="tab"
                aria-selected={v.id === view.id}
                disabled={!usable}
                title={usable ? undefined : "该位置达标球员不足 4 人（出场需达本队已踢时间的 40% 以上）"}
                className={v.id === view.id ? styles.tabOn : styles.tab}
                onClick={() => setViewId(v.id)}
              >
                {v.tab}
              </button>
            );
          })}
        </div>
      </header>

      {outcomeVarianceBanner && <p className={styles.outcomeVarianceBanner}>{outcomeVarianceBanner}</p>}

      <div
        ref={boxRef}
        className={styles.chartBox}
        onClick={handleBoxClick}
        data-crest-layout={
          process.env.NODE_ENV !== "production" && derived.layout
            ? JSON.stringify(
                drawn.map((p, i) => [
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
          showSummary={false}
          onEvents={{ click: handleChartClick }}
        />
      </div>

      <PlayerQuadrantDetail
        view={view}
        rows={positionRows}
        selected={selected}
        mx={mx}
        my={my}
        onRemove={(key) => setSelectedKeys((prev) => prev.filter((k) => k !== key))}
        onClear={() => setSelectedKeys([])}
      />

      <div className={styles.legend}>
        {byQuadrant
          .filter((q) => q.players.length)
          .map((q) => (
            <div key={q.label} className={styles.legendRow}>
              <span className={styles.legendKey}>{q.label}</span>
              <span className={styles.legendVal}>
                {q.players.map((p) => (
                  <button
                    key={p.key}
                    type="button"
                    className={selectedKeys.includes(p.key) ? styles.legendTeamOn : styles.legendTeam}
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

      <details className={styles.noteDetails}>
        <summary className={styles.noteSummary}>视角说明</summary>
        <p className={styles.note}>
          {view.note} 图上每名球员用头像表示,位置挤在一起的会自动错开一点避免遮挡;
          精确数值以点击后的详情面板为准。点击头像或上方名单查看该球员数值与位置内排名,
          再点一人可对比,按 Esc 或点空白处取消。头像来自 FotMob 图床,加载失败不影响坐标位置。
          {truncationText && <> {truncationText}</>}
          {hiddenText && <> {hiddenText}</>}
          {disabledViews.hasMissing && (
            <> 灰掉的视角是该位置数据源没有提供对应指标,不是本站算不出来。</>
          )}
          {disabledViews.hasSample && (
            <> 另有视角是数据源有,但该位置达标球员不足 4 人,暂不可用。</>
          )}
        </p>
      </details>
    </section>
  );
}
