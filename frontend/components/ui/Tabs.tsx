"use client";

/**
 * 全站唯一的「页面级切换」组件(2026-09-26,手机端体验第二批)。
 *
 * 取代此前各处自绘的下划线链接 / 药丸按钮 / 分段控件:联赛二级导航、比赛详情
 * 各 tab、精选页三个标签、排名页榜别切换全部走这一个组件。
 * 样式规格:横向可滚动、选中态 = 底部粗线(3px)+ 主色文字,不使用带下划线的超链接样式;
 * 选中项会自动滚进可视区(手机上项目多时不会藏在屏幕外)。
 *
 * 两种形态,由 items 是否带 href 决定:
 * - 链接形态(每一项都有 href):<nav> + <a aria-current="page">,用于路由级切换
 *   (联赛二级导航、榜别、精选标签)——URL 是状态,刷新/分享/后退都保持;
 * - 按钮形态(没有 href):role=tablist/tab,用于同页内切换(比赛详情的 tab),
 *   支持 ←/→ 键盘切换,面板关联由调用方通过 id / controls 传入。
 * 两种形态视觉完全一致。
 */

import Link from "next/link";
import { useEffect, useRef, type KeyboardEvent, type ReactNode } from "react";
import styles from "./Tabs.module.css";

export type TabItem = {
  key: string;
  label: ReactNode;
  /** 有 href → 链接形态;没有 → 按钮形态 */
  href?: string;
  /** 按钮形态:tab 的 DOM id / 它控制的面板 id(a11y) */
  id?: string;
  controls?: string;
  testId?: string;
};

export function Tabs({
  items,
  activeKey,
  ariaLabel,
  onSelect,
  className,
}: {
  items: TabItem[];
  activeKey: string;
  ariaLabel: string;
  /** 按钮形态必传:点击 / 键盘切换时回调 */
  onSelect?: (key: string) => void;
  className?: string;
}) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const linkMode = items.length > 0 && items.every((i) => i.href);

  // 选中项滚进可视区:只改横向 scrollLeft,不用 scrollIntoView(会顺带滚动整页)
  useEffect(() => {
    const scroller = scrollerRef.current;
    const el = scroller?.querySelector<HTMLElement>('[data-active="true"]');
    if (!scroller || !el) return;
    const target = el.offsetLeft - (scroller.clientWidth - el.offsetWidth) / 2;
    scroller.scrollLeft = Math.max(0, target);
  }, [activeKey]);

  const onKeyDown = (e: KeyboardEvent) => {
    if (linkMode || !onSelect) return;
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const idx = items.findIndex((t) => t.key === activeKey);
    const next = e.key === "ArrowRight" ? (idx + 1) % items.length : (idx + items.length - 1) % items.length;
    onSelect(items[next].key);
    const nextId = items[next].id;
    if (nextId) (document.getElementById(nextId) as HTMLElement | null)?.focus();
  };

  const cls = className ? `${styles.tabs} ${className}` : styles.tabs;

  if (linkMode) {
    return (
      <nav className={cls} aria-label={ariaLabel}>
        <div className={styles.scroller} ref={scrollerRef}>
          {items.map((t) => {
            const active = t.key === activeKey;
            return (
              <Link
                key={t.key}
                href={t.href!}
                className={active ? styles.tabActive : styles.tab}
                aria-current={active ? "page" : undefined}
                data-active={active}
                data-testid={t.testId}
              >
                {t.label}
              </Link>
            );
          })}
        </div>
      </nav>
    );
  }

  return (
    <div className={cls}>
      <div className={styles.scroller} ref={scrollerRef} role="tablist" aria-label={ariaLabel} onKeyDown={onKeyDown}>
        {items.map((t) => {
          const active = t.key === activeKey;
          return (
            <button
              key={t.key}
              id={t.id}
              type="button"
              role="tab"
              aria-selected={active}
              aria-controls={t.controls}
              tabIndex={active ? 0 : -1}
              className={active ? styles.tabActive : styles.tab}
              data-active={active}
              data-testid={t.testId}
              onClick={() => onSelect?.(t.key)}
            >
              {t.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
