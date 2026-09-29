"use client";

import { useMatchColors } from "@/components/charts/useMatchColors";

/** 模拟器全站统一的主客色:与 XgRaceChart 同源(useMatchColors 的兜底组合,主队青绿、客队橙),
 * 按真实渲染背景分别解析——卡片底("surface")与中性球场底("pitch")。
 * 动画射门点、势头条、事件条、结果页以及之后的分享图都只从这里取色。 */
export function useSimTeamColors(): { surface: [string, string]; pitch: [string, string] } {
  const s = useMatchColors({}, "surface").resolved;
  const p = useMatchColors({}, "pitch").resolved;
  return { surface: [s.home, s.away], pitch: [p.home, p.away] };
}
