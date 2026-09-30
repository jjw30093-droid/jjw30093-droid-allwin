// 选人面板的候选与排序(纯逻辑)。参照 FotMob Lineup Builder 的"点位置 → 面板里按该位置推荐本队球员 + 搜索",
// 区别:只能从本队阵容里选(不是自由组队),且不提供"移除球员"(模拟必须 11 人)。

import { fPos, type TeamSetup } from "./engine";
import { displayName } from "./labels";
import type { PlayerParams, SimParams } from "./types";

/** 出场不足这么多分钟的球员默认隐藏(有搜索词或"显示全部"时不隐藏) */
export const MIN_MINUTES_DEFAULT = 90;

export interface PickerRow {
  player: PlayerParams;
  /** 与该位置的兼容系数(1 同组、0.9 相邻、0.7 其它、0.3 门将互换) */
  fit: number;
  /** 已在首发里的位置下标(选中即互换);替补为 null */
  inXiSlot: number | null;
}

export interface PickerRows {
  current: PlayerParams | null;
  suggested: PickerRow[];
  others: PickerRow[];
  hiddenLowMinutes: number;
}

function norm(s: string): string {
  return s.normalize("NFKC").trim().toLowerCase();
}

/** 中文名 / 英文名(不分大小写)子串,或球衣号精确匹配。 */
export function matchesPlayerQuery(p: PlayerParams, query: string): boolean {
  const q = norm(query);
  if (!q) return true;
  if (p.shirt_number && norm(p.shirt_number) === q) return true;
  return [p.name_zh, p.name_en].some((n) => !!n && norm(n).includes(q));
}

export function pickerRows(
  params: SimParams,
  setup: TeamSetup,
  slotIndex: number,
  opts: { query?: string; showAll?: boolean } = {},
): PickerRows {
  const slot = setup.slots[slotIndex];
  const team = params.teams[String(setup.teamId)];
  const query = opts.query ?? "";
  const current = slot.playerId ? (params.players[slot.playerId] ?? null) : null;
  const xiSlot = new Map<string, number>();
  setup.slots.forEach((s, i) => {
    if (s.playerId) xiSlot.set(s.playerId, i);
  });
  const candidates = team.squad
    .filter((id) => id !== slot.playerId && params.players[id])
    .map((id) => params.players[id]);
  const filtered = candidates.filter((p) => matchesPlayerQuery(p, query));
  const visible = query || opts.showAll ? filtered : filtered.filter((p) => p.minutes >= MIN_MINUTES_DEFAULT);
  const hiddenLowMinutes = filtered.length - visible.length;
  const rows: PickerRow[] = visible
    .map((p) => ({ player: p, fit: fPos(p.main_position, slot.group), inXiSlot: xiSlot.get(p.player_id) ?? null }))
    .sort((a, b) => b.fit - a.fit || b.player.minutes - a.player.minutes || displayName(a.player).localeCompare(displayName(b.player), "zh"));
  return {
    current,
    suggested: rows.filter((r) => r.fit === 1),
    others: rows.filter((r) => r.fit < 1),
    hiddenLowMinutes,
  };
}
