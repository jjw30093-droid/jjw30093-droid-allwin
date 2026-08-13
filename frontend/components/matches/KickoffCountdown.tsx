"use client";

/**
 * 距开球倒计时——真实精确 kickoff_at_utc 算出的真倒计时(CLAUDE.md §11.2
 * 禁止假倒计时,这里是真实赛程时间,不是诱导性的虚构限时)。
 *
 * 初始渲染(含 SSR)不读 Date.now(),避免服务端渲染时刻与客户端水合时刻不同
 * 而产生文本不一致的水合警告;真正的时间差只在挂载后的 useEffect 里算,
 * 挂载前不占位(父组件已经在旁边展示了完整的开球时间文本)。
 */

import { useEffect, useState } from "react";

function formatCountdown(ms: number): string {
  if (ms <= 0) return "已开球";
  const totalMinutes = Math.floor(ms / 60_000);
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;
  if (days > 0) return `${days}天${hours}小时后开球`;
  if (hours > 0) return `${hours}小时${minutes}分钟后开球`;
  return `${minutes}分钟后开球`;
}

export function KickoffCountdown({ iso }: { iso: string }) {
  const [nowMs, setNowMs] = useState<number | null>(null);

  useEffect(() => {
    const tick = () => setNowMs(Date.now());
    // setTimeout(0) 而不是挂载时同步调用 setState——避免 effect 内联发起的
    // 级联渲染(react-hooks/set-state-in-effect),0ms 延迟对倒计时精度无感知。
    const kickoff = setTimeout(tick, 0);
    const id = setInterval(tick, 30_000);
    return () => {
      clearTimeout(kickoff);
      clearInterval(id);
    };
  }, []);

  if (nowMs === null) return null;
  return <span className="num">{formatCountdown(new Date(iso).getTime() - nowMs)}</span>;
}
