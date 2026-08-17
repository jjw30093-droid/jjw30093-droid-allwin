"use client";

/**
 * 模块二:球队风格定位(象限图)。
 *
 * 沿用 components/league/TeamQuadrantChart.tsx 的模式:多视角 pill、联赛均值虚线切四象限、
 * 中文象限名、有效点 < 4 时该视角禁用并说明原因(不静默回退、不补 0)。
 * 与联赛页的差别:本场两队高亮(队徽 + 队名 rich label),其余球队降为灰点背景 ——
 * 这里要回答的是「这两队在联赛分布里站哪」,不是给全联赛排序。
 *
 * 图表走全站唯一封装 components/EChart.tsx,ariaSummary 必填(CLAUDE.md §11.2)。
 */

import { useMemo, useState, useSyncExternalStore } from "react";
import type { EChartsOption } from "echarts";
import { EChart } from "@/components/EChart";
import { teamInitials } from "@/components/teams/TeamBadge";
import styles from "./MatchDataModules.module.css";
import pageStyles from "@/app/matches/[matchId]/match-detail.module.css";
import type { components } from "@/lib/api-types";

type StyleView = components["schemas"]["MatchPreviewStyleViewDTO"];
type StylePoint = components["schemas"]["MatchPreviewStylePointDTO"];

const THEME_CHANGE_EVENT = "allwin-theme-change";

type ChartColors = {
  teal: string;
  navy: string;
  ink2: string;
  ink3: string;
  grey: string;
  surface: string;
};

/** SSR/首次渲染安全的字面量兜底(浅色值)——真实值在挂载后由 readChartColors() 覆盖。 */
const FALLBACK_COLORS: ChartColors = {
  teal: "#087e78",
  navy: "#1d6f8b",
  ink2: "#40535d",
  ink3: "#5a6b73",
  grey: "#b8c6c6",
  surface: "#ffffff",
};

/**
 * ECharts canvas 不认 CSS 变量,必须读运行期解析值(同
 * components/matches/OddsTimeline.tsx 的既有做法)——而且不能直接读
 * --brand-navy:该 token 在深色模式下改写成 #061923,和深色模式的页面
 * 背景(#07161e)几乎同色,当"客队"高亮色会糊到看不见。这里改用
 * --brand-blue,深浅两色都是明确可辨识的中亮度蓝,专门作为图表第二
 * 强调色使用(该 token 此前在全站未被使用)。
 */
function readChartColors(): ChartColors {
  const style = getComputedStyle(document.documentElement);
  const readVar = (name: string, fallback: string) =>
    style.getPropertyValue(name).trim() || fallback;
  return {
    teal: readVar("--brand-teal", FALLBACK_COLORS.teal),
    navy: readVar("--brand-blue", FALLBACK_COLORS.navy),
    ink2: readVar("--ink-2", FALLBACK_COLORS.ink2),
    ink3: readVar("--ink-3", FALLBACK_COLORS.ink3),
    grey: readVar("--border-strong", FALLBACK_COLORS.grey),
    surface: readVar("--surface", FALLBACK_COLORS.surface),
  };
}

// useSyncExternalStore 要求 getSnapshot 在值未变时返回同一引用(Object.is),
// 否则每次渲染都判定"变了"造成无限重渲染——模块级缓存 + 逐字段比较,
// 只有主题真的切换时才产生新对象。
let cachedColors: ChartColors | null = null;
function getChartColorsSnapshot(): ChartColors {
  const next = readChartColors();
  if (
    cachedColors &&
    cachedColors.teal === next.teal &&
    cachedColors.navy === next.navy &&
    cachedColors.ink2 === next.ink2 &&
    cachedColors.ink3 === next.ink3 &&
    cachedColors.grey === next.grey &&
    cachedColors.surface === next.surface
  ) {
    return cachedColors;
  }
  cachedColors = next;
  return next;
}

function subscribeThemeChange(callback: () => void) {
  window.addEventListener(THEME_CHANGE_EVENT, callback);
  return () => window.removeEventListener(THEME_CHANGE_EVENT, callback);
}

/** 深浅色切换时重新解析 CSS 变量;服务端渲染与首帧水合前给字面量兜底。 */
function useChartColors(): ChartColors {
  return useSyncExternalStore(
    subscribeThemeChange,
    getChartColorsSnapshot,
    () => FALLBACK_COLORS,
  );
}

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
 * (例如 xg-for-against 视角把 idx0="对攻型" 而不是"两头都强")——
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

export function TeamStyleQuadrant({
  views,
  homeTeamId,
  awayTeamId,
  homeName,
  awayName,
  windowNote,
}: {
  views: StyleView[];
  homeTeamId: number;
  awayTeamId: number;
  homeName: string;
  awayName: string;
  /** 「近 5 场 · 2026-05-10 至 2026-08-09」——必须带真实日期区间(CLAUDE.md 措辞纪律) */
  windowNote: string;
}) {
  const available = useMemo(() => views.filter(usable), [views]);
  const [viewId, setViewId] = useState<string | null>(null);
  const view = available.find((v) => v.id === viewId) ?? available[0];

  const c = useChartColors();

  if (!view) {
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

  const pts = view.points.filter(
    (p): p is StylePoint & { x: number; y: number } => p.x != null && p.y != null,
  );
  const mx = mean(pts.map((p) => p.x));
  const my = mean(pts.map((p) => p.y));
  const sideOf = (p: StylePoint) =>
    p.team_id === homeTeamId ? "home" : p.team_id === awayTeamId ? "away" : null;
  const fmt = (v: number) => v.toFixed(view.digits);
  const quadOf = (p: { x: number; y: number }) => view.quadrants[quadrantIndex(p, mx, my)];

  const option: EChartsOption = {
    grid: { left: 42, right: 22, top: 26, bottom: 42 },
    xAxis: {
      type: "value",
      name: view.x_label,
      nameLocation: "middle",
      nameGap: 24,
      nameTextStyle: { color: c.ink2, fontSize: 11 },
      scale: true,
      boundaryGap: ["14%", "14%"],
      axisLabel: { color: c.ink2, fontSize: 11 },
      splitLine: { lineStyle: { opacity: 0.1 } },
    },
    yAxis: {
      type: "value",
      name: view.y_label,
      nameLocation: "end",
      nameGap: 12,
      nameTextStyle: { color: c.ink2, fontSize: 11, align: "left" },
      scale: true,
      boundaryGap: ["16%", "16%"],
      axisLabel: { color: c.ink2, fontSize: 11 },
      splitLine: { lineStyle: { opacity: 0.1 } },
    },
    tooltip: {
      confine: true,
      trigger: "item",
      formatter: (p: unknown) => {
        const d = (p as { data: { pt: StylePoint & { x: number; y: number } } }).data.pt;
        return (
          `<b>${d.name}</b>(${quadOf(d)})<br/>` +
          `${view.x_label} ${fmt(d.x)}(均值 ${fmt(mx)})<br/>` +
          `${view.y_label} ${fmt(d.y)}(均值 ${fmt(my)})`
        );
      },
    },
    series: [
      {
        type: "scatter",
        data: pts.map((p) => {
          const side = sideOf(p);
          return {
            value: [p.x, p.y],
            pt: p,
            side,
            symbolSize: side ? 17 : 11,
            itemStyle: {
              color: side === "home" ? c.teal : side === "away" ? c.navy : c.grey,
              opacity: side ? 1 : 0.85,
              borderColor: side ? "#fff" : "transparent",
              borderWidth: side ? 2 : 0,
            },
          };
        }),
        label: {
          show: true,
          position: "top",
          distance: 6,
          // 本场两队:队徽(TeamBadge 同一套 initials 规则)+ 队名;
          // 其余球队用 --ink-2 + 半透明面色垫底,--ink-3 压在网格线上看不清。
          formatter: (p: unknown) => {
            const d = (p as { data: { pt: StylePoint; side: string | null } }).data;
            if (!d.side) return `{o|${d.pt.name}}`;
            return `{crest${d.side}|${teamInitials(d.pt.name)}}{n|${d.pt.name}}`;
          },
          rich: {
            cresthome: {
              backgroundColor: c.teal,
              color: "#fff",
              fontSize: 10,
              fontWeight: 700,
              padding: [3, 3, 2, 3],
              borderRadius: 2,
              align: "center",
            },
            crestaway: {
              backgroundColor: c.navy,
              color: "#fff",
              fontSize: 10,
              fontWeight: 700,
              padding: [3, 3, 2, 3],
              borderRadius: 2,
              align: "center",
            },
            n: { color: c.navy, fontSize: 11.5, fontWeight: 700, padding: [0, 0, 0, 4] },
            o: {
              color: c.ink2,
              fontSize: 11,
              backgroundColor: c.surface,
              padding: [2, 3],
              borderRadius: 2,
            },
          },
        },
        labelLayout: { moveOverlap: "shiftY", hideOverlap: true },
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { color: c.ink3, type: "dashed", opacity: 0.5 },
          label: { show: false },
          data: [{ xAxis: mx }, { yAxis: my }],
        },
      },
    ],
  };

  const home = pts.find((p) => p.team_id === homeTeamId);
  const away = pts.find((p) => p.team_id === awayTeamId);
  const ariaSummary =
    `${view.title}象限图,共 ${pts.length} 支球队。` +
    `虚线是这 ${pts.length} 支球队${windowNote}的平均值(${view.x_label} ${fmt(mx)},${view.y_label} ${fmt(my)}),` +
    `只在联赛内部比较,不能跨联赛。` +
    (home ? `${homeName} ${fmt(home.x)} / ${fmt(home.y)},落在「${quadOf(home)}」。` : `${homeName} 缺该视角数据。`) +
    (away ? `${awayName} ${fmt(away.x)} / ${fmt(away.y)},落在「${quadOf(away)}」。` : `${awayName} 缺该视角数据。`) +
    `描述的是${windowNote}怎么踢,不是对本场的预测。`;

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
        <EChart option={option} height={320} ariaSummary={ariaSummary} showSummary={false} />
        <div className={styles.legendRow}>
          <span className={styles.legendItem}>
            <span className={styles.swatchDot} style={{ background: c.teal }} />
            {homeName}
          </span>
          <span className={styles.legendItem}>
            <span className={styles.swatchDot} style={{ background: c.navy }} />
            {awayName}
          </span>
          <span className={styles.legendItem}>
            <span className={styles.swatchDot} style={{ background: c.grey }} />
            联赛其余球队
          </span>
        </div>
        <p className={styles.summary}>{ariaSummary}</p>
      </div>
    </section>
  );
}
