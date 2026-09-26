/**
 * 全站唯一的「筛选类」控件(2026-09-26,手机端体验第二批):时间 / 联赛 / 状态 / 赛季 …
 * 这类"在同一个列表里选一个条件"的药丸。和 Tabs(页面级切换)是全站仅有的两种。
 *
 * - 有 href → 站内链接(URL 是筛选状态,服务端渲染友好);没有 href → 按钮(aria-pressed);
 * - 触控区 ≥ 44px 高(CLAUDE.md §11.2),只用 padding/最小高度撑开,不放大字号。
 * 一行放不下时用 ChipRow 包起来(横向滚动 + 右侧渐隐提示)。
 */

import Link from "next/link";
import type { ReactNode } from "react";
import styles from "./Chip.module.css";

export function Chip({
  active = false,
  href,
  onClick,
  children,
  testId,
  ariaLabel,
}: {
  active?: boolean;
  href?: string;
  onClick?: () => void;
  children: ReactNode;
  testId?: string;
  ariaLabel?: string;
}) {
  const cls = active ? styles.chipActive : styles.chip;
  if (href) {
    return (
      <Link href={href} className={cls} aria-current={active ? "true" : undefined} aria-label={ariaLabel} data-testid={testId}>
        {children}
      </Link>
    );
  }
  return (
    <button type="button" className={cls} aria-pressed={active} aria-label={ariaLabel} data-testid={testId} onClick={onClick}>
      {children}
    </button>
  );
}
