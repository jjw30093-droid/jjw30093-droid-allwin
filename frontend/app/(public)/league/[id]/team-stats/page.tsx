import { fetchLeagueOverview, type TeamStatsRow } from "@/lib/api";
import { LeagueNav } from "@/components/LeagueNav";
import { LeaderboardCard, type LeaderboardRow } from "@/components/LeaderboardCard";
import styles from "./team-stats.module.css";

// 只用免费字段(CLAUDE.md §3):射门/射正/控球/xG/xGOT。角球/黄红牌/零封/
// BTTS 是付费字段,overview 本来就不返回,这里也没有取 betting endpoint。
const METRICS: {
  key: keyof TeamStatsRow;
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
  teamStats: TeamStatsRow[],
  metric: (typeof METRICS)[number]
): LeaderboardRow[] {
  return teamStats
    .filter((t) => t[metric.key] !== null)
    .sort((a, b) => (b[metric.key] as number) - (a[metric.key] as number))
    .slice(0, 10)
    .map((t, i) => ({
      rank: i + 1,
      name: t.team_name_zh ?? `Team ${t.team_id}`,
      value: metric.format(t[metric.key] as number),
    }));
}

export default async function TeamStatsPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ season?: string }>;
}) {
  const { id } = await params;
  const { season: seasonParam } = await searchParams;

  let data;
  try {
    data = await fetchLeagueOverview(id, seasonParam);
  } catch (err) {
    return (
      <main className={styles.page}>
        <LeagueNav leagueId={id} active="team-stats" season={seasonParam} />
        <div className={styles.errorBox}>
          <div className={styles.errorTitle}>数据暂时无法加载</div>
          <p>
            后端 serving API 未响应,请确认 backend/api_server.py 已启动。
            <br />
            <span style={{ fontSize: 12, color: "var(--ink-3)" }}>
              {err instanceof Error ? err.message : String(err)}
            </span>
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className={styles.page}>
      <LeagueNav leagueId={id} active="team-stats" season={data.season} />
      <div className={styles.header}>
        <h1 className={styles.title}>英超 · 球队数据榜</h1>
        <span className={styles.seasonChip}>{data.season}</span>
      </div>

      <div className={styles.grid}>
        {METRICS.map((metric) => (
          <LeaderboardCard
            key={metric.key}
            title={metric.title}
            rows={topRows(data.team_stats, metric)}
          />
        ))}
      </div>
    </main>
  );
}
