/**
 * 球员榜分组(2026-09-26):后端 28 个球员榜是一张平铺列表(没有分类字段),
 * 手机上太长,按与球队数据榜相同的四段结构分组:重点数据 / 进攻 / 防守 / 纪律,
 * 供榜单区域顶部的吸顶分组条使用。
 *
 * 只按 stat_name 归类,不改变任何榜单的数据、排序或口径;没登记过的新榜落进
 * 「其他」(该组只在有内容时才出现),不会消失。
 */

export type PlayerBoardGroupKey = "top" | "attack" | "defend" | "discipline" | "other";

export const PLAYER_GROUP_TITLES: Record<PlayerBoardGroupKey, string> = {
  top: "重点数据",
  attack: "进攻",
  defend: "防守",
  discipline: "纪律",
  other: "其他",
};

export const PLAYER_GROUP_ORDER: PlayerBoardGroupKey[] = ["top", "attack", "defend", "discipline", "other"];

const GROUP_OF: Record<string, PlayerBoardGroupKey> = {
  // 重点数据
  goals: "top",
  goal_assists: "top",
  player_rating: "top",
  expected_goals: "top",
  expected_assists: "top",
  // 进攻(含创造机会、传球、过人)
  expected_goals_on_target: "attack",
  shots_total: "attack",
  shots_on_target: "attack",
  big_chances_created: "attack",
  big_chances_missed: "attack",
  passes_completed: "attack",
  long_passes_completed: "attack",
  dribbles_completed: "attack",
  poss_won_att_3rd: "attack",
  penalties_won: "attack",
  // 防守(含门将)
  tackles: "defend",
  interceptions: "defend",
  ball_recoveries: "defend",
  clearances: "defend",
  shots_blocked_defensively: "defend",
  defensive_contributions: "defend",
  saves: "defend",
  clean_sheets: "defend",
  goals_conceded: "defend",
  // 纪律
  yellow_cards: "discipline",
  red_cards: "discipline",
  fouls_committed: "discipline",
  penalties_conceded: "discipline",
};

export function playerGroupOf(statName: string): PlayerBoardGroupKey {
  return GROUP_OF[statName] ?? "other";
}
