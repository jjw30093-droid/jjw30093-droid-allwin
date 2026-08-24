/**
 * 关键事件判定(2026-08-25)。单独一个**无 "use client" 指令**的模块——
 * OverviewKeyEvents.tsx 是 client 组件,从它导出的一切在服务端都只是
 * client reference,MatchDetailBody(随导入方环境走,SSR 下是 Server
 * Component)调用会直接抛
 * "Attempted to call hasKeyEvents() from the server"(2026-08-25 真实
 * 浏览器验证抓到的 bug,jsdom 单测不模拟 RSC 边界所以全绿)。判定逻辑放
 * 这里供两侧共用,谓词只此一份不漂移。
 */

import type { MatchReportResponse } from "@/lib/api-v1";

type MatchReport = Extract<MatchReportResponse, { available: true }>;
export type MatchReportEvent = MatchReport["events"][number];

export function isKeyEvent(e: MatchReportEvent): boolean {
  return e.event_type === "Goal" || e.event_type === "Card";
}

/** 调用方判空用:没有任何关键事件时整节(含「关键事件」标题)不渲染,
 * 不留一个只有标题的空节。 */
export function hasKeyEvents(events: MatchReportEvent[]): boolean {
  return events.some(isKeyEvent);
}
