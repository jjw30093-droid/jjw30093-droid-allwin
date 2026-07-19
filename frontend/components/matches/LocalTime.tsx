"use client";

/**
 * UTC 时间戳按用户时区展示(宪法 §11.2:数据库统一 UTC,展示按用户时区)。
 * SSR 与首次水合渲染确定性的 UTC 文本,水合完成后直接换算浏览器时区——
 * useSyncExternalStore 区分服务端/客户端快照,无 setState、无水合冲突。
 */

import { useSyncExternalStore } from "react";
import { utcFallback } from "./zh";

const emptySubscribe = () => () => {};

function useHydrated(): boolean {
  return useSyncExternalStore(
    emptySubscribe,
    () => true,
    () => false,
  );
}

function formatLocal(iso: string): string | null {
  const normalized = iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`;
  const d = new Date(normalized);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString("zh-CN", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function LocalTime({
  iso,
  fallback = "—",
}: {
  iso?: string | null;
  fallback?: string;
}) {
  const hydrated = useHydrated();
  if (!iso) return <>{fallback}</>;
  const local = hydrated ? formatLocal(iso) : null;
  return (
    <time dateTime={iso} suppressHydrationWarning>
      {local ?? utcFallback(iso)}
    </time>
  );
}
