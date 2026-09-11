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
export function formatBoardValue(
  value: number | null | undefined,
  format: string | null | undefined,
  decimals: number | null | undefined
): string | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  switch (format) {
    case "percent":
      return `${value.toFixed(decimals ?? 1)}%`;
    case "meter":
      // 场均跑动是 118540.1 米这种量级,按米原样显示没人读得出来
      return `${(value / 1000).toFixed(1)} km`;
    case "number":
      return String(Math.round(value));
    case "fraction":
      return value.toFixed(decimals ?? 1);
    default:
      return value.toFixed(decimals ?? 0);
  }
}

export function boardTitle(board: TeamSourceBoard): string {
  if (board.per_match === true) return `${board.label_zh} · 场均`;
  if (board.per_match === false) return `${board.label_zh} · 赛季合计`;
  return board.label_zh;
}
