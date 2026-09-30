"use client";

import { useEffect, useRef, type ReactNode } from "react";
import styles from "./simulator.module.css";

/** 通用面板壳(照搬 components/trust/MobileFollowBar.tsx::FollowSheet 的做法):
 *  role=dialog、Esc / 点遮罩关闭、锁住页面滚动、打开时聚焦第一个输入框、关闭时把焦点还给打开它的元素。
 *  <600px 是底部抽屉,≥600px 是居中弹窗(见 .sheetOverlay / .sheet)。 */
export function Sheet({ title, onClose, children, testId }: { title: string; onClose: () => void; children: ReactNode; testId?: string }) {
  const boxRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const first = boxRef.current?.querySelector<HTMLElement>("input, button");
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
      opener?.focus?.();
    };
  }, [onClose]);
  return (
    <div className={styles.sheetOverlay} onClick={onClose} data-testid={testId ? `${testId}-overlay` : undefined}>
      <div
        ref={boxRef}
        className={styles.sheet}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        data-testid={testId}
      >
        {children}
      </div>
    </div>
  );
}
