"use client";

/**
 * 「加载更多」列表(2026-09-26 精选页「历史战绩」):默认只显示前 pageSize 条,
 * 底部按钮每次再多显示 pageSize 条。
 *
 * 子元素由调用方(可能是服务端组件)渲染好传进来,这里只负责切片与计数——
 * 这样不需要把 SlipCard 这类展示组件挪进客户端边界,也不会和 TrackRecordPanel
 * 形成循环引用。全部条目仍在 RSC/HTML 里(与改造前一致),只是初始不挂到 DOM。
 */

import { Children, useState, type ReactNode } from "react";
import styles from "./LoadMoreList.module.css";

export function LoadMoreList({
  children,
  pageSize = 10,
}: {
  children: ReactNode;
  pageSize?: number;
}) {
  const items = Children.toArray(children);
  const [shown, setShown] = useState(pageSize);
  const remaining = items.length - shown;

  return (
    <>
      {items.slice(0, shown)}
      {remaining > 0 && (
        <button type="button" className={styles.more} onClick={() => setShown((n) => n + pageSize)}>
          加载更多
          <span className={styles.count}>（还有 {remaining} 条）</span>
        </button>
      )}
    </>
  );
}
