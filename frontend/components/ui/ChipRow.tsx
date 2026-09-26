"use client";

/**
 * 一排 Chip:一行放不下时横向滚动,内容还能继续往右滑时右侧加渐隐遮罩提示
 * (滑到头自动去掉,放得下时不加,避免把最后一个 chip 无谓地淡掉)。
 * label 是这一排的名字("时间"/"联赛"),放在滚动区左边、不参与滚动。
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import styles from "./ChipRow.module.css";

export function ChipRow({
  label,
  ariaLabel,
  children,
}: {
  label?: string;
  ariaLabel?: string;
  children: ReactNode;
}) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const [fadeRight, setFadeRight] = useState(false);

  const measure = useCallback(() => {
    const el = scrollerRef.current;
    if (!el) return;
    setFadeRight(el.scrollWidth - el.clientWidth - el.scrollLeft > 4);
  }, []);

  useEffect(() => {
    measure();
    const el = scrollerRef.current;
    if (!el) return;
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(measure) : null;
    ro?.observe(el);
    window.addEventListener("resize", measure);
    return () => {
      ro?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [measure]);

  return (
    <div className={styles.row} role="group" aria-label={ariaLabel ?? label}>
      {label && <span className={styles.label}>{label}</span>}
      <div
        ref={scrollerRef}
        className={styles.scroller}
        data-fade-right={fadeRight}
        onScroll={measure}
      >
        {children}
      </div>
    </div>
  );
}
