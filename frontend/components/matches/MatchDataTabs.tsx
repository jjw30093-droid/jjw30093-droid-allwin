"use client";

/**
 * 「数据」tab 内的子 tab:阵容 / 风格 / 球员。
 * 视觉与 MatchPreTabs 刻意不同 —— 父级是下划线 tab,子级是 pill,
 * 避免两层 tab 长得一样分不清层级。pill 写法取自 TeamQuadrantChart 的视角切换器。
 *
 * 某个子页整块无数据时不隐藏 pill,由子页自己渲染诚实空态
 * (隐藏 pill 会让读者以为这个站没有这个功能)。
 */

import { useState, type ReactNode } from "react";
import styles from "./MatchDataTabs.module.css";

export type DataTabKey = "lineup" | "style" | "players";

const TABS: { key: DataTabKey; label: string }[] = [
  { key: "lineup", label: "阵容" },
  { key: "style", label: "风格" },
  { key: "players", label: "球员" },
];

export function MatchDataTabs({
  lineup,
  style,
  players,
}: {
  lineup: ReactNode;
  style: ReactNode;
  players: ReactNode;
}) {
  const [active, setActive] = useState<DataTabKey>("lineup");
  const panels: Record<DataTabKey, ReactNode> = { lineup, style, players };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const idx = TABS.findIndex((t) => t.key === active);
    const next =
      e.key === "ArrowRight"
        ? (idx + 1) % TABS.length
        : (idx + TABS.length - 1) % TABS.length;
    setActive(TABS[next].key);
    document.getElementById(`match-datatab-${TABS[next].key}`)?.focus();
  };

  return (
    <div>
      <div
        role="tablist"
        aria-label="数据模块切换"
        className={styles.tablist}
        onKeyDown={onKeyDown}
      >
        {TABS.map((t) => (
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
      {TABS.map((t) => (
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
