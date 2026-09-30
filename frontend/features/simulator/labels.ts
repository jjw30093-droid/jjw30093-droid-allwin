// 位置组中文名与球员显示名(纯函数,无 "use client":服务端组件与客户端组件都可 import,CLAUDE.md §11.4)。

import type { PlayerParams, PosGroup } from "./types";

export const POS_LABEL: Record<PosGroup, string> = {
  GK: "门将", CB: "中卫", FB: "边后卫", DM: "后腰", CM: "中场", AM: "前腰", W: "边锋", ST: "中锋",
};

// 中文名 → 英文名;任何情况下都不显示球员 id。
export function displayName(p: PlayerParams): string {
  return p.name_zh || p.name_en || "未知球员";
}

/**
 * 阵型槽位 x → 画面横向位置(0 = 左边缘,1 = 右边缘),用于"门将在下、向上进攻"的画法(排阵球场卡、分享图阵容)。
 *
 * 参数里的 x 取自 FotMob 阵容的 verticalLayout.x,**x 小 = 该队的右路**:生产库 2026/27 五大联赛首发
 * position_id 32(右后卫,如达洛特)恒为 0.125、38(左后卫,如卢克·肖、卡拉菲奥里)恒为 0.875,
 * 83(右边锋,如萨卡)0.162,主客队一致。FotMob 竖版画的是向下进攻的队,所以直接用 x 在"向上进攻"的画面里
 * 会左右颠倒(2026-09-30 站长发现:曼联卢克·肖出现在右后卫位置)。这里统一镜像,旧分享链接里的阵容同样纠正。
 */
export function slotScreenX(x: number): number {
  return 1 - x;
}
