"use client";

/**
 * 比赛详情页赛前/进行中 tab(看点/数据/赔率)——2026-08-14 重设计(Claude
 * Design 定稿):平铺态原来 6 个区块竖着铺一整屏,现在按"手机一屏一件事"
 * 重组为三段,内容一项不减,只是分组。视觉/无障碍写法与 MatchTabs 共用
 * 同一份 tablist 样式(MatchTabs.module.css),布局用等宽 3 列而不是横滚。
 */

import { useState, type ReactNode } from "react";
import { Tabs } from "@/components/ui/Tabs";
import styles from "./MatchTabs.module.css";

export type MatchPreTabKey = "highlights" | "data" | "odds";

const TABS: { key: MatchPreTabKey; label: string }[] = [
  { key: "highlights", label: "看点" },
  { key: "data", label: "数据" },
  { key: "odds", label: "赔率" },
];

export function MatchPreTabs({
  highlights,
  data,
  odds,
}: {
  highlights: ReactNode;
  data: ReactNode;
  odds: ReactNode;
}) {
  const [active, setActive] = useState<MatchPreTabKey>("highlights");

  const panels: Record<MatchPreTabKey, ReactNode> = { highlights, data, odds };

  return (
    <div className={styles.wrap}>
      <Tabs
        ariaLabel="比赛内容切换"
        activeKey={active}
        onSelect={(k) => setActive(k as MatchPreTabKey)}
        items={TABS.map((t) => ({
          key: t.key,
          label: t.label,
          id: `match-pretab-${t.key}`,
          controls: `match-prepanel-${t.key}`,
        }))}
      />
      {TABS.map((t) => (
        <div
          key={t.key}
          id={`match-prepanel-${t.key}`}
          role="tabpanel"
          aria-labelledby={`match-pretab-${t.key}`}
          hidden={active !== t.key}
        >
          {panels[t.key]}
        </div>
      ))}
    </div>
  );
}
