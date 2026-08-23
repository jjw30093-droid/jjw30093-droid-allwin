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

function formatCountdown(ms: number, variant: "suffix" | "prefix"): string {
  if (ms <= 0) return "已开球";
  const totalMinutes = Math.floor(ms / 60_000);
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;
  if (variant === "prefix") {
    // 比赛详情页头部:开球时间已经单独一行展示,这里只补"还有多久",
    // 不重复"开球"两个字;精确到小时,不像首页那样细到分钟。
    if (days > 0) return `还有 ${days} 天 ${hours} 小时`;
    if (hours > 0) return `还有 ${hours} 小时`;
    return `还有 ${minutes} 分钟`;
  }
  if (days > 0) return `${days}天${hours}小时后开球`;
  if (hours > 0) return `${hours}小时${minutes}分钟后开球`;
  return `${minutes}分钟后开球`;
}

export function KickoffCountdown({
  iso,
  variant = "suffix",
  showPrefixDot = false,
}: {
  iso: string;
  /** "suffix"(默认,首页用):"1天3小时后开球"。"prefix"(比赛详情页头部用):
   * "还有 1 天 3 小时",配合旁边已经展示的完整开球时间使用。 */
  variant?: "suffix" | "prefix";
  /** prefix 场景下,是否在倒计时前自带"北京时间 · "这个分隔符。挂载前本
   * 组件整体返回 null,分隔符必须跟着倒计时文字一起出现/消失——写死在
   * 父组件里会在 hydration 完成前露出一个孤零零的"·"。 */
  showPrefixDot?: boolean;
}) {
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
  const text = formatCountdown(new Date(iso).getTime() - nowMs, variant);
  return <span className="num">{showPrefixDot ? `北京时间 · ${text}` : text}</span>;
}
