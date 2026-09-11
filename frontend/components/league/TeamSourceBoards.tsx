/**
 * 来源方赛季球队榜(纯展示)。消费 /api/v1/leagues/{id}/team-stats 的 boards。
 *
 * 与同页上方的 TeamStatsBoards 是**两套口径**:那边是我们自己从单场数据聚合的
 * silver_team_season_stats,这边是来源方自己算的赛季榜。后端已经把会撞车的
 * 维度(控球/射门/射正/xG/角球/犯规/红黄牌/零封)排除在 boards 之外,所以这里
 * 不会出现同一个指标两个数字 —— 选择逻辑在 backend/queries/league_stats.py::
 * FREE_TEAM_BOARDS,前端不重复一份白名单。
 *
 * 单位("场均"还是"赛季合计")一律读后端下发的 per_match,不在前端按字段名猜:
 * 同一批榜里 poss_won_att_3rd_team 是场均、big_chance_team 是赛季合计,而两者的
 * stat_format 都可能是 'fraction'。per_match 为 null 表示来源没给标题、无法判断,
 * 此时不标单位(宁可不标,不猜)。
 */

import { LeaderboardCard, type LeaderboardRow } from "@/components/LeaderboardCard";
import type { TeamSourceBoard } from "@/lib/api-v1";
import styles from "./boards.module.css";

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

function toRows(board: TeamSourceBoard): LeaderboardRow[] {
  const rows: LeaderboardRow[] = [];
  board.entries.forEach((e, i) => {
    const value = formatBoardValue(e.value, board.stat_format, board.stat_decimals);
    // 没有数值的行不占位:排名榜里显示一个空值等于骗人说"这队排第 N"
    if (value == null) return;
    rows.push({
      rank: e.rank ?? i + 1,
      name: e.team.name,
      value,
      avatar: { kind: "team", crestUrl: e.team.crest_url },
      teamColor: e.team_color,
    });
  });
  return rows;
}

export function TeamSourceBoards({ boards }: { boards: TeamSourceBoard[] }) {
  const usable = boards
    .map((b) => ({ board: b, rows: toRows(b) }))
    .filter((x) => x.rows.length > 0);
  // 一条榜都没有(老赛季还没采过)就整块不渲染,不摆一墙"暂无数据"的空卡片
  if (usable.length === 0) return null;

  return (
    <section className={styles.sourceSection}>
      <h2 className={styles.sourceHeader}>更多球队数据</h2>
      <p className={styles.sourceNote}>
        以下榜单直接来自数据源的赛季统计，与上方我们自己按单场数据聚合的榜单口径不同，
        因此不会出现同一项指标的两个数字。
      </p>
      <div className={styles.grid}>
        {usable.map(({ board, rows }) => (
          <LeaderboardCard key={board.stat_name} title={boardTitle(board)} rows={rows} />
        ))}
      </div>
    </section>
  );
}
