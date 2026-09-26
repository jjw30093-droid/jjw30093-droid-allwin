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
import { teamKey, windowScaleFor } from "./teamMetrics";
import { buildQuadrantOption } from "./quadrantOption";
import {
  ANNOTATION_BADGES,
  VIEWS,
  VIEW_GROUPS,
  dirsOf,
  filterWindowLabel,
  fmt,
  groupedViews,
  buildConclusion,
  hiddenNote,
  isSmallSample,
  pickAnnotations,
  mean,
  meanBandHalf,
  nearMeanTeams,
  outlierNames,
  plotSet,
  quadrantOf,
  resolveClickedKey,
  resolveSelectedTeams,
  toggleSelection,
  type PlotSet,
  type Pt,
  type View,
  type ViewGroupId,
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

/** 禁用文案分两种,不能混成一句:有数但样本不够 vs 数据源根本没给。
 *  后者原文案 e2e 逐字依赖,不能改。`filtered` 时样本不足的提示额外提醒
 *  用户放宽筛选窗口可能就有数据了——这一支球队不是永久没数据,只是在当前
 *  筛选窗口下不够,不能让用户误以为是数据源的问题。 */
function disabledReason(p: PlotSet, filtered: boolean): string {
  const sampleHidden = p.hidden.filter((h) => h.reason === "sample").length;
  if (p.pts.length + sampleHidden < 4) return "该联赛该赛季缺少此视角所需的数据";
  return filtered
    ? "当前筛选窗口下样本达标的球队不足 4 支，试试放宽筛选范围"
    : "该联赛该赛季样本达标的球队不足 4 支";
}

/** 分类行同样要分清"样本不够"（赛季初的正常状态,随轮次增加会自动解锁）
 *  和"数据源真没有"（不会自动变好）——只要该类别下所有暂不可用的视角都是
 *  样本原因,就不该说成"缺少数据",那会被赛季初的用户误读成本站永久没有
 *  这类数据(见 2026-09-15 真实反馈:英超新赛季踢了 4 轮,这类分类全灰,
 *  站长问"照道理应该有数据了",根因就是这句话把"还没攒够样本"说成了
 *  "没有这个维度")。只要有一个视角是真的缺数据,才维持严格的"缺少数据"
 *  措辞,不能为了安慰用户而对结构性缺失也说"再等等"。 */
function groupDisabledReason(groupViews: View[], plots: Map<string, PlotSet>, filtered: boolean): string {
  const allSampleOnly = groupViews.every((v) => {
    const p = plots.get(v.id)!;
    const sampleHidden = p.hidden.filter((h) => h.reason === "sample").length;
    return p.pts.length + sampleHidden >= 4;
  });
  if (!allSampleOnly) return "该联赛该赛季缺少这一类视角所需的数据";
  return filtered
    ? "当前筛选窗口下这一类视角样本达标的球队都不足 4 支，试试放宽筛选范围"
    : "该联赛该赛季这一类视角样本达标的球队还不足 4 支，随着轮次增加会自动解锁";
}

/** 球队象限图自己的 grid:比共用的 QUADRANT_GRID 多留出上/下两行,
 *  给轴端"往哪边读是好"的方向提示(见 quadrantOption.ts 的 axisHints)。
 *  共用的 QUADRANT_GRID 不动——球员象限图与比赛页象限图不受影响。 */
const TEAM_GRID = { ...QUADRANT_GRID, top: 58, bottom: 66 };

/** 宽度变化小于这个像素数不重算布局,避免拖动窗口时反复重排 */
const WIDTH_HYSTERESIS = 8;

/** 窄屏(<480)队徽最小 28px(2026-09-26),20 队塞进 300 宽的绘图区会很挤,
 *  所以窄屏每队多给纵向空间。 */
const NARROW_WIDTH = 480;
const NARROW_CREST_MIN = 28;

function chartHeightFor(n: number, narrow: boolean) {
  // 球队越多越需要纵向空间给队徽错开,宽屏 20 队 → 380、窄屏 20 队 → 520;
  // 另加 TEAM_GRID 比共用 grid 多出的上下留白
  const perTeam = narrow ? 26 : 19;
  return (
    Math.max(340, n * perTeam) + (TEAM_GRID.top - QUADRANT_GRID.top) + (TEAM_GRID.bottom - QUADRANT_GRID.bottom)
  );
}

export function TeamQuadrantChart({
  rows,
  recency,
  venue,
}: {
  rows: TeamSeasonStatRow[];
  /** 2026-09-14"最近 N 场/主客场"筛选新增,均可选——由 team-stats/page.tsx
   *  从 searchParams 透传。两者都缺省时行为与筛选功能上线前逐字节相同。 */
  recency?: number;
  venue?: string;
}) {
  const c = useChartColors();
  // 样本门槛按当前筛选窗口等比缩小(见 teamMetrics.ts::windowScaleFor)——
  // "最近 3 场"下继续套用整赛季门槛会让几乎所有比率型指标判定样本不足。
  const windowScale = windowScaleFor(recency, venue);
  const filtered = recency != null || (venue != null && venue !== "all");
  // 每个视角各自算一次点集与隐藏名单(样本门槛是指标自己的属性,视角越多
  // 这张 Map 越有用:切视角不用重新扫一遍全部 rows)。
  const plots = useMemo(
    () => new Map(VIEWS.map((v) => [v.id, plotSet(rows, v, windowScale)] as const)),
    [rows, windowScale],
  );
  // 攻防视角依赖数据源的 xg 档,不是每个联赛赛季都有;新视角还可能因样本门槛
  // 不达标而不可用 —— 先算可用性再定默认视角。
  const available = useMemo(
    () => VIEWS.filter((v) => (plots.get(v.id)?.pts.length ?? 0) >= 4),
    [plots],
  );
  const [viewId, setViewId] = useState<string | null>(null);
  const [groupId, setGroupId] = useState<ViewGroupId | null>(null);
  const view = available.find((v) => v.id === viewId) ?? available[0];
  const activeGroup = groupId ?? view?.group ?? VIEW_GROUPS[0].id;
  const pickGroup = (g: ViewGroupId) => {
    setGroupId(g);
    // 类别一换就把图切到该类别下第一个可用视角 —— 不留"选了类别但图还停在
    // 另一个类别的视角"这种对不上的状态,不需要 useEffect,点击里一次做完。
    const first = available.find((v) => v.group === g) ?? VIEWS.find((v) => v.group === g);
    if (first) setViewId(first.id);
  };
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [helpOpen, setHelpOpen] = useState(false);

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

  // plots.get(...) 本身是稳定引用(同一个 plots 不变就返回同一个对象),
  // 但包一层 useMemo 让 React Compiler 能静态确认 pts/hidden 的稳定性,
  // 下游好几个 useMemo 都依赖它们,写成裸解构会让编译器放弃优化整个组件。
  const { pts, hidden } = useMemo<PlotSet>(
    () => (view ? plots.get(view.id)! : { pts: [], hidden: [] }),
    [plots, view],
  );
  // 门槛开启后,均值/排名只应统计真正画在图上的球队 —— 否则详情面板的
  // "均值 X" 会和虚线代表的均值对不上(两处必须同一份 pts 派生)。
  const eligibleRows = useMemo(() => {
    if (!pts.length) return [];
    const onChart = new Set(pts.map((p) => p.key));
    return rows.filter((r) => onChart.has(teamKey(r.team)));
  }, [rows, pts]);

  const derived = useMemo(() => {
    if (!view || pts.length < 4) return null;
    const mx = mean(pts.map((p) => p.x));
    const my = mean(pts.map((p) => p.y));
    const dirs = dirsOf(view);
    const lowY = dirs.y === true;
    // 端点 = 数据范围两端各留 8% 余量,不吸附到刻度(见 niceAxisRange 的 snap)
    const xr = niceAxisRange(pts.map((p) => p.x), { pad: 0.08, snap: false, targetTicks: 6 });
    const yr = niceAxisRange(pts.map((p) => p.y), { pad: 0.08, snap: false, targetTicks: 6 });
    const narrow = width != null && width < NARROW_WIDTH;
    const height = chartHeightFor(pts.length, narrow);
    const box: PlotBox | null = width ? { width, height, grid: TEAM_GRID } : null;
    const crestSize = box
      ? crestSizeFor(box, pts.length, narrow ? { min: NARROW_CREST_MIN } : undefined)
      : CREST.MIN + 6;
    // 均值临界带:均值线 ±5%,带内球队在名单里标"临界"
    const bandX = meanBandHalf(mx, pts.map((p) => p.x));
    const bandY = meanBandHalf(my, pts.map((p) => p.y));
    const layout = box
      ? layoutCrests({ pts, box, xr, yr, yInverse: lowY, radius: crestSize / 2 + CREST.PAD })
      : null;
    const annotations = pickAnnotations(pts, view, mx, my);
    return { mx, my, dirs, lowY, xr, yr, height, crestSize, layout, bandX, bandY, annotations };
  }, [view, pts, width]);

  const selected = useMemo(() => resolveSelectedTeams(pts, selectedKeys), [pts, selectedKeys]);

  const option = useMemo(() => {
    if (!view || !derived) return null;
    const { mx, my, xr, yr, crestSize, layout, bandX, bandY, height: chartHeight, annotations } = derived;
    // 窄屏队名标签更容易压在相邻队徽上,只标最极端的 4 支;宽屏 6 支
    const labelled = outlierNames(pts, mx, my, width != null && width < 480 ? 4 : 6);
    for (const p of pts) if (!p.crestUrl) labelled.add(p.name);
    for (const p of selected) labelled.add(p.name);
    // 自动标注的球队一律要求显示队名,并带序号前缀,与图下的标注列表对应
    // (是否真的显示仍受队徽碰撞检测约束——压住别的队徽的标签宁可摘掉)
    const badges: Record<string, string> = {};
    for (const a of annotations) {
      labelled.add(a.name);
      badges[a.name] = ANNOTATION_BADGES[a.no - 1] ?? "";
    }
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
      grid: TEAM_GRID,
      axisHints: true,
      badges,
      // 均值线标注:未筛选叫"联赛平均"(统计的是画出的球队,同本联赛本赛季);
      // 筛了就换成窗口名,不能继续叫"联赛平均"却只统计了最近几场
      enhance: {
        meanLabel: filtered ? `${filterWindowLabel(recency, venue)}平均` : "联赛平均",
        bandX,
        bandY,
        box: width ? { width, height: chartHeight } : null,
      },
      selectedIndexes: selected.map((s) => pts.indexOf(s)),
    });
  }, [view, derived, pts, selected, c, width, filtered, recency, venue]);

  if (!view || !derived || !option) {
    return (
      <p className={styles.empty}>
        该联赛该赛季的球队指标不足以绘制象限图（有效球队少于 4 支）。
      </p>
    );
  }

  const { mx, my, dirs, height, bandX, bandY } = derived;
  const quad = (p: Pt) => quadrantOf(p, mx, my, dirs);
  const borderline = nearMeanTeams(pts, mx, my, bandX, bandY);
  const { annotations } = derived;
  const meanName = filtered ? `${filterWindowLabel(recency, venue)}平均` : "联赛平均";
  const conclusion = buildConclusion(pts, view, mx, my, meanName);
  const smallSample = isSmallSample(pts);

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

  // outcome_variance 语义免责声明:只要这个视角有一根轴是"短期结果记录,
  // 不是稳定能力"(如场均终结超额/场均门将扑救超额),就固定展示这行提示,
  // 而不是散落进各视角的 note 里各写一遍——新增同语义指标自动获得这条提示,
  // 不需要逐个视角补文案。
  const outcomeVarianceBanner =
    view.x.semantic === "outcome_variance" || view.y.semantic === "outcome_variance"
      ? "这个视角展示的是本赛季至今的结果记录，不是稳定的能力评价，赛季间相关性低，不代表未来表现。"
      : null;

  const byQuadrant = view.quadrants.map((label, i) => ({
    label,
    teams: pts.filter((p) => quad(p) === i),
  }));

  const hiddenText = hiddenNote(hidden, view, windowScale);
  const windowLabel = filterWindowLabel(recency, venue);

  // 灰掉的 tab 分两种原因,脚注措辞不能混为一谈:数据源真没给 vs 有数但样本不够。
  const disabledViews = VIEWS.filter((v) => !available.some((a) => a.id === v.id)).reduce(
    (acc, v) => {
      const p = plots.get(v.id)!;
      const sampleHidden = p.hidden.filter((h) => h.reason === "sample").length;
      if (p.pts.length + sampleHidden >= 4) acc.hasSample = true;
      else acc.hasMissing = true;
      return acc;
    },
    { hasMissing: false, hasSample: false },
  );

  // 摘要措辞与改造前逐字一致(e2e 依赖),有选中时只在末尾追加一句;
  // hiddenText 插在"共 N 支球队…"之后、"参考线是联赛内部平均…"之前 ——
  // 没有隐藏球队时 hiddenText 是空串,这句话与改造前逐字节相同。
  const ariaSummary =
    `${view.title}象限图，虚线是${windowLabel}平均值（${view.x.label} ${fmt(mx, view.x)}，` +
    `${view.y.label} ${fmt(my, view.y)}）。` +
    byQuadrant
      .filter((q) => q.teams.length)
      .map((q) => `${q.label}：${q.teams.map((p) => p.name).join("、")}`)
      .join("；") +
    `。共 ${pts.length} 支球队${sampleNote ? `，${sampleNote}` : ""}。` +
    (hiddenText ? `${hiddenText}` : "") +
    `参考线是联赛内部平均，不能拿来跨联赛比较。` +
    `${conclusion}` +
    (annotations.length ? `图上标注：${annotations.map((a) => `${a.name}（${a.reasons.join("；")}）`).join("；")}。` : "") +
    (smallSample ? "样本较小，排位还会变。" : "") +
    (borderline.size
      ? `距均值线 5% 以内的临界球队：${pts.filter((p) => borderline.has(p.key)).map((p) => p.name).join("、")}。`
      : "") +
    (selected.length
      ? `当前选中：${selected.map((p) => `${p.name}（${view.quadrants[quad(p)]}）`).join("、")}。`
      : "");

  return (
    <section className={styles.card}>
      <header className={styles.head}>
        <div className={styles.headTop}>
          <div>
            <h2 className={styles.title}>球队象限图</h2>
            <p className={styles.sub}>
              虚线为{windowLabel}平均
              {sampleNote ? ` · ${sampleNote}` : ""}
            </p>
          </div>
        </div>

        {/* 一句自动结论 + 样本偏小提示(2026-09-26"有结论"):结论纯由数据模板生成 */}
        <div className={styles.verdict}>
          <p className={styles.conclusion}>{conclusion}</p>
          {smallSample && <span className={styles.smallSample}>样本较小，排位还会变</span>}
        </div>

        {/* 类别行是筛选器,不是第二层 tablist——role="group" + aria-pressed,
            与下方图例的球队按钮同一套交互模式,不用 role="radio"(会承诺
            未实现的方向键导航)也不嵌套 tablist(无效标记)。 */}
        <div className={styles.groups} role="group" aria-label="象限图视角分类">
          {groupedViews().map(({ group, views: groupViews }) => {
            const usable = groupViews.some((v) => available.some((a) => a.id === v.id));
            return (
              <button
                key={group.id}
                type="button"
                aria-pressed={group.id === activeGroup}
                disabled={!usable}
                title={usable ? group.blurb : groupDisabledReason(groupViews, plots, filtered)}
                className={group.id === activeGroup ? styles.groupOn : styles.group}
                onClick={() => pickGroup(group.id)}
              >
                {group.label}
              </button>
            );
          })}
        </div>

        <div className={styles.tabs} role="tablist" aria-label="象限图视角">
          {VIEWS.filter((v) => v.group === activeGroup).map((v) => {
            const usable = available.some((a) => a.id === v.id);
            return (
              <button
                key={v.id}
                type="button"
                role="tab"
                aria-selected={v.id === view.id}
                disabled={!usable}
                title={usable ? undefined : disabledReason(plots.get(v.id)!, filtered)}
                className={v.id === view.id ? styles.tabOn : styles.tab}
                onClick={() => setViewId(v.id)}
              >
                {v.tab}
              </button>
            );
          })}
        </div>

        {/* 图上方只放一句话;操作说明与完整口径收进"?"提示(2026-09-26) */}
        <div className={styles.summaryRow}>
          <p className={styles.summary}>{view.summary}</p>
          <button
            type="button"
            className={helpOpen ? styles.helpBtnOn : styles.helpBtn}
            aria-expanded={helpOpen}
            aria-controls="team-quadrant-help"
            aria-label="查看操作说明与口径"
            onClick={() => setHelpOpen((o) => !o)}
          >
            ?
          </button>
        </div>
        {helpOpen && (
          <div id="team-quadrant-help" className={styles.help} role="region" aria-label="操作说明与口径">
            <p className={styles.note}>{view.note}</p>
            <p className={styles.note}>
              点击队徽或下方名单查看该队数值与联赛排名，再点一支可对比，按 Esc 或点空白处取消。
              位置挤在一起的队徽会自动错开一点避免遮挡，精确数值以详情面板为准。
            </p>
            <p className={styles.note}>
              图上均值线两侧的淡色带是均值 ±5% 的“临界带”；名单里标“临界”的球队至少有一项指标落在带内，
              属于哪个象限很容易随几场比赛变化，不要过度解读。
              轴上有负值的指标（如终结超额）按数据跨度的 5% 计算。
            </p>
            {(disabledViews.hasMissing || disabledViews.hasSample) && (
              <p className={styles.note}>
                {disabledViews.hasMissing && <>灰掉的视角是该联赛该赛季数据源没有提供对应指标，不是本站算不出来。</>}
                {disabledViews.hasSample && (
                  <>
                    {" "}
                    另有视角是数据源有，但样本达标的球队不足 4 支，暂不可用
                    {filtered ? "，放宽筛选范围可能就有数据了" : ""}。
                  </>
                )}
              </p>
            )}
          </div>
        )}
      </header>

      {outcomeVarianceBanner && <p className={styles.outcomeVarianceBanner}>{outcomeVarianceBanner}</p>}

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
          showSummary={false}
          onEvents={{ click: handleChartClick }}
        />
      </div>

      {/* 图上标注:队名前的序号 ①②③ 与这里对应;文字全部由数据生成 */}
      {annotations.length > 0 && (
        <ol className={styles.annotations} aria-label="图上自动标注">
          {annotations.map((a) => (
            <li key={a.key} className={styles.annotation}>
              <span className={styles.annotationNo}>{ANNOTATION_BADGES[a.no - 1]}</span>
              <span>
                <strong>{a.name}</strong>：{a.reasons.join("；")}
              </span>
            </li>
          ))}
        </ol>
      )}

      <TeamQuadrantDetail
        view={view}
        rows={eligibleRows}
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
                    {borderline.has(p.key) && (
                      <span className={styles.borderlineTag} title="距均值线 5% 以内，归属的象限很容易随几场比赛变化">
                        临界
                      </span>
                    )}
                  </button>
                ))}
              </span>
            </div>
          ))}
      </div>

      {/* 藏了几支球队必须在可见区域说清楚,不能只放在折叠提示里 */}
      {hiddenText && <p className={styles.hiddenNote}>{hiddenText}</p>}
    </section>
  );
}
