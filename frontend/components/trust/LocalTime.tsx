"use client";

/**
 * UTC 时间戳 → 用户本地时区展示。
 * 服务端渲染与 hydration 首帧输出 UTC 文本(带 UTC 标注,诚实不装本地),
 * hydration 完成后的渲染直接换算浏览器时区——用 useSyncExternalStore 区分
 * 服务端/客户端快照,无 setState、无 hydration 冲突。
 */

import { useSyncExternalStore } from "react";

type Mode = "date" | "datetime";

const emptySubscribe = () => () => {};

function useHydrated(): boolean {
  return useSyncExternalStore(
    emptySubscribe,
    () => true,
    () => false,
  );
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

function formatUtcFallback(iso: string, mode: Mode): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const date = `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
  return mode === "datetime"
    ? `${date} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`
    : `${date}(UTC)`;
}

function formatLocal(iso: string, mode: Mode): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const date = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  return mode === "datetime" ? `${date} ${pad(d.getHours())}:${pad(d.getMinutes())}` : date;
}

export function LocalTime({ utc, mode = "datetime" }: { utc: string; mode?: Mode }) {
  const hydrated = useHydrated();
  return (
    <time dateTime={utc}>
      {hydrated ? formatLocal(utc, mode) : formatUtcFallback(utc, mode)}
    </time>
  );
}
