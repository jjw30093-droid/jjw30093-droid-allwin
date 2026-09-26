"use client";

/**
 * 榜单区域顶部的吸顶分组切换(球队数据榜 / 球员榜共用,2026-09-26):
 * 点某一项 → 平滑滚动到对应分组;滚动时高亮当前所在分组。
 *
 * 吸顶位置在站点顶栏正下方(top = --site-header-h),z-index 低于顶栏、
 * 底部导航是 fixed 在页面底部,两者互不遮挡;条本身在榜单区域内才吸顶
 * (sticky 受父容器约束,榜单结束后跟着滚走,不会盖住下方内容/页脚)。
 * 分组标题的 scroll-margin-top 已把"顶栏 + 吸顶条"的高度让出来,
 * 滚动落点不会被吸顶条盖住。
 */

import { useEffect, useState } from "react";
import { Tabs } from "@/components/ui/Tabs";
import { boardSectionDomId } from "./boardSections";
import styles from "./BoardSectionTabs.module.css";

/** 吸顶条高度(px),须与 BoardSectionTabs.module.css 的 .tabs min-height 一致 */
const BAR_H = 44;

export type BoardSection = { id: string; label: string };

export function BoardSectionTabs({ sections }: { sections: BoardSection[] }) {
  const [active, setActive] = useState(sections[0]?.id ?? "");

  // 滚动时高亮"顶部已经越过的最后一个分组"
  useEffect(() => {
    if (sections.length < 2) return;
    let raf = 0;
    const update = () => {
      raf = 0;
      const header = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--site-header-h")) || 0;
      const line = header + BAR_H + 8; // 顶栏 + 吸顶条 + 余量
      let current = sections[0].id;
      for (const s of sections) {
        const el = document.getElementById(boardSectionDomId(s.id));
        if (el && el.getBoundingClientRect().top <= line) current = s.id;
      }
      setActive(current);
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(update);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    update();
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [sections]);

  if (sections.length < 2) return null;

  const jump = (id: string) => {
    setActive(id);
    document
      .getElementById(boardSectionDomId(id))
      ?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  };

  return (
    <div className={styles.bar} data-testid="board-section-tabs">
      <Tabs
        ariaLabel="榜单分组"
        className={styles.tabs}
        activeKey={active}
        onSelect={jump}
        items={sections.map((s) => ({ key: s.id, label: s.label, id: `board-tab-${s.id}` }))}
      />
    </div>
  );
}
