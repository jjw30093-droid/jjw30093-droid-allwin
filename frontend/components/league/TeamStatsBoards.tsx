/**
 * 球队数据榜(纯展示)。消费 /api/v1/leagues/{id}/team-stats 的免费字段投影
 * (射门/射正/控球/xG/xGOT——付费深度字段物理上不在响应里,这里也没有)。
 */

import { LeaderboardCard, type LeaderboardRow } from "@/components/LeaderboardCard";
import type { TeamSeasonStatRow } from "@/lib/api-v1";
import styles from "./boards.module.css";

const METRICS: {
  key: keyof Omit<TeamSeasonStatRow, "team" | "matches_played">;
  title: string;
  format: (v: number) => string;
}[] = [
  { key: "avg_total_shots", title: "场均射门榜", format: (v) => v.toFixed(1) },
  { key: "avg_shots_on_target", title: "场均射正榜", format: (v) => v.toFixed(1) },
  { key: "avg_possession", title: "控球率榜", format: (v) => `${v.toFixed(1)}%` },
  { key: "avg_expected_goals", title: "场均 xG 榜", format: (v) => v.toFixed(2) },
  {
    key: "avg_expected_goals_on_target",
    title: "场均 xGOT 榜",
    format: (v) => v.toFixed(2),
  },
];

function topRows(
  rows: TeamSeasonStatRow[],
  metric: (typeof METRICS)[number]
): LeaderboardRow[] {
  return rows
    .filter((t) => t[metric.key] != null)
    .sort((a, b) => (b[metric.key] as number) - (a[metric.key] as number))
    .slice(0, 10)
    .map((t, i) => ({
      rank: i + 1,
      name: t.team.name,
      value: metric.format(t[metric.key] as number),
    }));
}

export function TeamStatsBoards({ rows }: { rows: TeamSeasonStatRow[] }) {
  return (
    <div className={styles.grid}>
      {METRICS.map((metric) => (
        <LeaderboardCard
          key={metric.key}
          title={metric.title}
          rows={topRows(rows, metric)}
        />
      ))}
    </div>
  );
}
