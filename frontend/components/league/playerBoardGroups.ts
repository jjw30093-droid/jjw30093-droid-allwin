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
  goal_assist: "top",
  rating: "top",
  expected_goals: "top",
  expected_assists: "top",
  // 进攻(含创造机会、传球、过人)
  expected_goalsontarget: "attack",
  total_scoring_att: "attack",
  ontarget_scoring_att: "attack",
  big_chance_created: "attack",
  big_chance_missed: "attack",
  accurate_pass: "attack",
  accurate_long_balls: "attack",
  won_contest: "attack",
  poss_won_att_3rd: "attack",
  penalty_won: "attack",
  // 防守(含门将)
  total_tackle: "defend",
  interception: "defend",
  ball_recovery: "defend",
  effective_clearance: "defend",
  outfielder_block: "defend",
  defensive_contributions: "defend",
  saves: "defend",
  clean_sheet: "defend",
  goals_conceded: "defend",
  // 纪律
  yellow_card: "discipline",
  red_card: "discipline",
  fouls: "discipline",
  penalty_conceded: "discipline",
};

export function playerGroupOf(statName: string): PlayerBoardGroupKey {
  return GROUP_OF[statName] ?? "other";
}
