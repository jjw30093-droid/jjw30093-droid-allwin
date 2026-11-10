/**
 * 来源方赛季榜的数值/标题格式化(2026-09-11 从 TeamSourceBoards.tsx 拆出)。
 *
 * 拆出来的原因:球队数据榜改成按语义分区后,来源方榜单不再单独成块渲染,
 * 而是和我们自己的指标一起按 重点/进攻/防守/纪律 混排(TeamStatsSections),
 * 原来那个组件没有存在的必要了;但这两个格式化函数仍然是必需的,而且是纯
 * 函数、值得单独测。
 *
 * 单位("场均"还是"赛季合计")一律读后端下发的 per_match,不在前端按字段名猜:
 * 同一批榜里 poss_won_att_3rd_team 是场均、big_chance_team 是赛季合计,而两者的
 * stat_format 都可能是 'fraction'。per_match 为 null 表示来源没给标题、无法判断,
 * 此时不标单位(宁可不标,不猜)。
 */

import type { TeamSourceBoard } from "@/lib/api-v1";

/** 按来源自报的 stat_format / stat_decimals 格式化,不按指标名硬编码小数位。 */
export function formatBoardValue(value: number | null | undefined, unit: string | null | undefined, decimals: number | null | undefined): string | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  if (unit === "metres") return `${(value / 1000).toFixed(1)} km`;
  if (unit === "percent") return `${value.toFixed(decimals ?? 1)}%`;
  return value.toFixed(decimals ?? (Number.isInteger(value) ? 0 : 1));
}

export function boardTitle(board: TeamSourceBoard): string {
  const bases = new Set(board.entries.map((e) => e.aggregation));
  if (bases.size !== 1) return board.label_zh;
  switch ([...bases][0]) {
    case "per_match": return `${board.label_zh} · 场均`;
    case "season_total": return `${board.label_zh} · 赛季合计`;
    case "per_90_minutes": return `${board.label_zh} · 每90分钟`;
    case "mean": return `${board.label_zh} · 平均`;
    default: return board.label_zh;
  }
}
