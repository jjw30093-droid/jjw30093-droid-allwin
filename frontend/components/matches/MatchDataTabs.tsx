"use client";

/**
 * 「数据/分析」tab 内的子 tab:阵容 / 风格 / 球员 / 射门。
 * 视觉与 MatchPreTabs 刻意不同 —— 父级是下划线 tab,子级是 pill,
 * 避免两层 tab 长得一样分不清层级。pill 写法取自 TeamQuadrantChart 的视角切换器。
 *
 * 某个子页整块无数据时不隐藏 pill,由子页自己渲染诚实空态
 * (隐藏 pill 会让读者以为这个站没有这个功能)。例外是 lineup 槽位本身
 * 不传(2026-08-25,已完赛的「分析」tab):不是"该子页没数据",而是
 * "预计阵容"对已完赛比赛这个概念整个不适用(真实首发在「阵容」tab)——
 * 概念不适用与数据缺失是两回事,前者删 pill、后者渲染空态。
 */

import { useState, type ReactNode } from "react";
import styles from "./MatchDataTabs.module.css";

export type DataTabKey = "lineup" | "style" | "players" | "shots";

const ALL_TABS: { key: DataTabKey; label: string }[] = [
  { key: "lineup", label: "阵容" },
  { key: "style", label: "风格" },
  { key: "players", label: "球员" },
  { key: "shots", label: "射门" },
];

export function MatchDataTabs({
  lineup,
  style,
  players,
  shots,
}: {
  /** 缺省(不传)= 本上下文没有"预计阵容"这个概念(已完赛),整个 pill
   * 不渲染,默认落到风格。 */
  lineup?: ReactNode;
  style: ReactNode;
  players: ReactNode;
  shots: ReactNode;
}) {
  const tabs = lineup === undefined ? ALL_TABS.filter((t) => t.key !== "lineup") : ALL_TABS;
  const [active, setActive] = useState<DataTabKey>(tabs[0].key);
  const panels: Partial<Record<DataTabKey, ReactNode>> = { lineup, style, players, shots };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const idx = tabs.findIndex((t) => t.key === active);
    const next =
      e.key === "ArrowRight"
        ? (idx + 1) % tabs.length
        : (idx + tabs.length - 1) % tabs.length;
    setActive(tabs[next].key);
    document.getElementById(`match-datatab-${tabs[next].key}`)?.focus();
  };

  return (
    <div>
      <div
        role="tablist"
        aria-label="数据模块切换"
        className={styles.tablist}
        onKeyDown={onKeyDown}
      >
        {tabs.map((t) => (
          <button
            key={t.key}
            id={`match-datatab-${t.key}`}
            role="tab"
            type="button"
            aria-selected={active === t.key}
            aria-controls={`match-datapanel-${t.key}`}
            tabIndex={active === t.key ? 0 : -1}
            className={active === t.key ? styles.pillOn : styles.pill}
            onClick={() => setActive(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tabs.map((t) => (
        <div
          key={t.key}
          id={`match-datapanel-${t.key}`}
          role="tabpanel"
          aria-labelledby={`match-datatab-${t.key}`}
          hidden={active !== t.key}
        >
          {panels[t.key]}
        </div>
      ))}
    </div>
  );
}
