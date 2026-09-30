// 位置组中文名与球员显示名(纯函数,无 "use client":服务端组件与客户端组件都可 import,CLAUDE.md §11.4)。

import type { PlayerParams, PosGroup } from "./types";

export const POS_LABEL: Record<PosGroup, string> = {
  GK: "门将", CB: "中卫", FB: "边后卫", DM: "后腰", CM: "中场", AM: "前腰", W: "边锋", ST: "中锋",
};

// 中文名 → 英文名;任何情况下都不显示球员 id。
export function displayName(p: PlayerParams): string {
  return p.name_zh || p.name_en || "未知球员";
}
