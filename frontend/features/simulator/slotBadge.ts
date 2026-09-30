// 球场位置右上角的徽标(参照 FotMob Lineup Builder 底栏的徽标切换;我们用已有数据:位置 / 出场时间 / 场均 xG)。

import { fPos } from "./engine";
import type { PlayerParams, PosGroup } from "./types";

export type BadgeMode = "position" | "minutes" | "xg";
export const BADGE_MODES: BadgeMode[] = ["position", "minutes", "xg"];
export const BADGE_LABEL: Record<BadgeMode, string> = { position: "位置", minutes: "出场时间", xg: "场均 xG" };
/** 球场底栏分段按钮用的短名(手机一行放得下);完整名放在 title / 读屏文字里 */
export const BADGE_SHORT: Record<BadgeMode, string> = { position: "位置", minutes: "出场", xg: "xG" };

export interface SlotBadge {
  /** 短文本(手机) */
  text: string;
  /** 完整文本(桌面);没有则与 text 相同 */
  long?: string;
  /** 位置错配等需要提醒的情况 */
  warn: boolean;
  /** 位置正确时的位置名:手机上不显示(半场只有 250px 高,5 人一排时会压到邻座) */
  quiet?: boolean;
}

export function slotBadge(player: PlayerParams | null, slotGroup: PosGroup, mode: BadgeMode): SlotBadge | null {
  if (!player) return null;
  if (mode === "minutes") return { text: `${player.minutes}'`, warn: false };
  if (mode === "xg") {
    // 缺失值不当 0(CLAUDE.md §11.3)
    return player.npxg90 == null ? { text: "—", warn: false } : { text: player.npxg90.toFixed(2), warn: false };
  }
  const f = fPos(player.main_position, slotGroup);
  if (f < 1) return { text: `×${f}`, long: `${player.main_position}→${slotGroup} ×${f}`, warn: true };
  return { text: slotGroup, warn: false, quiet: true };
}
