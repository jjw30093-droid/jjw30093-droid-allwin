/**
 * 球队数据榜的分区定义(2026-09-11)。
 *
 * 改造前这一页是按**数据出处**分的:上面一坨是我们自己从单场聚合的 5 个
 * 指标,下面一坨叫「更多球队数据」是来源方的赛季榜。站长的评价是"又是一坨"
 * ——出处是实现细节,读者关心的是"这是进攻还是防守",所以改成按足球语义分区。
 *
 * 分区取 FotMob 自己的四段(实证来源有两条,不是猜的):
 * 1. 站长提供的真机截图 ~/Downloads/fotmob/fotmob球队数据1.png 里,球队数据
 *    tab 第一段的标题就是「重点数据」;
 * 2. 我们采下来的 fact_season_team_stats.extra_json 里每个指标都带 FotMob
 *    自报的 `category` 字段,取值恰好是 Top Stat / Attacking / Defending /
 *    Discipline 四种。
 * 中文名同样取自 FotMob 中文包(resources.arsc 里存在「重点数据」「进攻」
 * 「防守」三个串);Discipline 在中文包里没有独立译名,「纪律」是站长定的。
 *
 * 来源方榜单的分区**不在这里写死**,直接读后端透传的 board.category(见
 * backend/queries/league_stats.py)——在前端抄一份指标→分组的映射,等于把
 * FotMob 的分类决策复制一遍,来源改了我们不会知道。这张表只负责两件事:
 * 分区的顺序/中文名,以及**我们自己那些指标**该归哪一区。
 */

import type { TeamSeasonStatRow } from "@/lib/api-v1";

/** 后端透传的来源分组值 → 我们的分区 key。来源没给分组的归 null(兜底区)。 */
export type SectionKey = "top" | "attack" | "defend" | "discipline";

export const SECTION_TITLES: Record<SectionKey, string> = {
  top: "重点数据",
  attack: "进攻",
  defend: "防守",
  discipline: "纪律",
};

/** 渲染顺序,与 FotMob 一致。 */
export const SECTION_ORDER: SectionKey[] = ["top", "attack", "defend", "discipline"];

export function sectionOfSourceCategory(category: string | null | undefined): SectionKey {
  switch (category) {
    case "Top Stat":
      return "top";
    case "Attacking":
      return "attack";
    case "Defending":
      return "defend";
    case "Discipline":
      return "discipline";
    default:
      // 来源新增了没见过的分组时归入「重点数据」,不静默丢弃整张榜
      return "top";
  }
}

/** 我们自己聚合的指标(silver_team_season_stats 投影)。 */
export type OwnMetric = {
  key: keyof Omit<TeamSeasonStatRow, "team" | "matches_played" | "team_color">;
  title: string;
  section: SectionKey;
  /**
   * 排序方向。**不能一律降序**:失球类指标越少越好,降序会把防守最差的队
   * 放在第 1 名、还给它一个队色胶囊,读起来像在表扬它。方向与来源方一致
   * (生产实测 FotMob 自己的 goals_conceded_team_match / expected_goals_
   * conceded_team 两张榜 rank=1 都是数值最小的那队)。
   */
  order: "desc" | "asc";
  format: (v: number) => string;
};

/**
 * 这里列的比改造前多 6 个(角球/零封/被创造 xG/场均犯规/黄牌/红牌)——它们
 * 一直在 TeamSeasonStatRow 里(2026-08-16 起全字段免费投影),但前端从来没有
 * 渲染过任何一张对应的卡,同时又因为"DTO 里已经有了"被排除在来源方榜单之外
 * (FREE_TEAM_BOARDS 的注释),两头一夹,整页一个都看不到。这次分区顺带补齐。
 *
 * BTTS(btts_matches/btts_pct)和 xG 三个拆解档(运动战/定位球/非点球)没有
 * 加进来:前者不是"排行"语义(两队都进球的场次多不代表这队强),后者与场均 xG
 * 同源、四张卡讲同一件事,会把进攻区撑成指标墙。
 */
export const OWN_METRICS: OwnMetric[] = [
  // ── 重点数据 ──
  { key: "avg_possession", title: "控球率", section: "top", order: "desc", format: (v) => `${v.toFixed(1)}%` },
  { key: "clean_sheets", title: "零封场次", section: "top", order: "desc", format: (v) => String(Math.round(v)) },
  // ── 进攻 ──
  { key: "avg_total_shots", title: "场均射门", section: "attack", order: "desc", format: (v) => v.toFixed(1) },
  { key: "avg_shots_on_target", title: "场均射正", section: "attack", order: "desc", format: (v) => v.toFixed(1) },
  { key: "avg_expected_goals", title: "场均 xG", section: "attack", order: "desc", format: (v) => v.toFixed(2) },
  { key: "avg_expected_goals_on_target", title: "场均 xGOT", section: "attack", order: "desc", format: (v) => v.toFixed(2) },
  { key: "avg_corners", title: "场均角球", section: "attack", order: "desc", format: (v) => v.toFixed(1) },
  // ── 防守 ──
  { key: "avg_expected_goals_conceded", title: "场均被创造 xG", section: "defend", order: "asc", format: (v) => v.toFixed(2) },
  // ── 纪律 ──
  { key: "avg_fouls", title: "场均犯规", section: "discipline", order: "desc", format: (v) => v.toFixed(1) },
  { key: "avg_yellow_cards", title: "场均黄牌", section: "discipline", order: "desc", format: (v) => v.toFixed(2) },
  { key: "avg_red_cards", title: "场均红牌", section: "discipline", order: "desc", format: (v) => v.toFixed(2) },
];

/**
 * 各分区**开头**的卡片顺序(卡片 key:own:<DTO 字段> / src:<来源 stat_name>)。
 * 没列到的排在后面、保持各自来源的原顺序——来源方将来多给几个指标不会因为
 * 这张表没列而消失,也不用每次跟着改。
 *
 * 为什么需要这张表:如果只按"我方指标在前、来源榜在后"排,「重点数据」会变成
 * 控球率/零封/场均进球/场均失球/跑动距离——进球和失球这两个最该先看的指标被
 * 挤到第 3、4 张。FotMob 自己的顺序是 评分/单场进球/单场失球/平均控球率/
 * 零失球/上场(站长提供的真机截图 fotmob球队数据1.png、2.png),这里对齐它
 * (评分与上座人数我们有意不做,见 FREE_TEAM_BOARDS 注释)。
 */
export const SECTION_LEAD: Record<SectionKey, string[]> = {
  top: [
    "src:goals_team_match",
    "src:goals_conceded_team_match",
    "own:avg_possession",
    "own:clean_sheets",
    "src:phys_tdc_team",
  ],
  attack: [
    "own:avg_total_shots",
    "own:avg_shots_on_target",
    "own:avg_expected_goals",
    "own:avg_expected_goals_on_target",
    "src:big_chance_team",
    "src:big_chance_missed_team",
    "src:touches_in_opp_box_team",
    "own:avg_corners",
  ],
  defend: [
    "own:avg_expected_goals_conceded",
    "src:total_tackle_team",
    "src:interception_team",
    "src:effective_clearance_team",
    "src:saves_team",
    "src:poss_won_att_3rd_team",
  ],
  discipline: ["own:avg_fouls", "own:avg_yellow_cards", "own:avg_red_cards"],
};
