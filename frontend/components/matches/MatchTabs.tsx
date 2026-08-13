"use client";

/**
 * 比赛详情页内 tab(总览/阵容/统计/事件)——仅完赛且 report 可用时由
 * MatchDetailBody 渲染;未完赛/无数据路径不经过本组件(不留空 tab 壳)。
 *
 * 设计要点:
 * - 四个面板都是 ReactNode 插槽 prop(RSC 内容可以跨 client 边界传入;
 *   render-prop 函数不行);
 * - 非激活面板用 hidden 属性而非条件卸载:全部内容保留在可爬取 HTML 里
 *   (本报告匿名可见,SEO 有效),切换零重挂载;
 * - 射门图的 ECharts 懒挂载在 ShotMapChart 内部用 IntersectionObserver 自治,
 *   不依赖本组件传状态(避免跨插槽耦合);
 * - 视觉沿用 LeagueNav.module.css 的 .active/.link 惯例(金色下划线)。
 */

import { useState, type ReactNode } from "react";
import styles from "./MatchTabs.module.css";

export type MatchTabKey = "shots" | "stats" | "lineup" | "events" | "overview";

/**
 * 顺序即优先级,第一个是默认。
 *
 * 2026-08-12 重排:此前默认落「总览」,而完赛后 QuickView 不渲染,总览里只剩
 * 证据块(已下架)和文字战绩列表 —— **默认第一屏是全页最弱的一屏**,
 * 而最强的射门内容藏在第三个 tab 里。现在射门(威胁时间轴 + xG 累积曲线 +
 * 射门落点图)排第一并设为默认,总览退到最后。
 */
const TABS: { key: MatchTabKey; label: string }[] = [
  { key: "shots", label: "射门" },
  { key: "stats", label: "统计" },
  { key: "lineup", label: "阵容" },
  { key: "events", label: "事件" },
  { key: "overview", label: "总览" },
];

export function MatchTabs({
  shots,
  overview,
  lineup,
  stats,
  events,
}: {
  shots: ReactNode;
  overview: ReactNode;
  lineup: ReactNode;
  stats: ReactNode;
  events: ReactNode;
}) {
  const [active, setActive] = useState<MatchTabKey>("shots");

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const idx = TABS.findIndex((t) => t.key === active);
    const next = e.key === "ArrowRight" ? (idx + 1) % TABS.length : (idx + TABS.length - 1) % TABS.length;
    setActive(TABS[next].key);
    (document.getElementById(`match-tab-${TABS[next].key}`) as HTMLElement | null)?.focus();
  };

  const panels: Record<MatchTabKey, ReactNode> = { shots, stats, lineup, events, overview };

  return (
    <div className={styles.wrap}>
      <div role="tablist" aria-label="比赛内容切换" className={styles.tablist} onKeyDown={onKeyDown}>
        {TABS.map((t) => (
          <button
            key={t.key}
            id={`match-tab-${t.key}`}
            role="tab"
            type="button"
            aria-selected={active === t.key}
            aria-controls={`match-panel-${t.key}`}
            tabIndex={active === t.key ? 0 : -1}
            className={active === t.key ? styles.active : styles.link}
            onClick={() => setActive(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {TABS.map((t) => (
        <div
          key={t.key}
          id={`match-panel-${t.key}`}
          role="tabpanel"
          aria-labelledby={`match-tab-${t.key}`}
          hidden={active !== t.key}
        >
          {panels[t.key]}
        </div>
      ))}
    </div>
  );
}
