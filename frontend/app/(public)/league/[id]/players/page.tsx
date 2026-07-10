import { fetchLeagueOverview } from "@/lib/api";
import { LeagueNav } from "@/components/LeagueNav";
import { LeaderboardCard, type LeaderboardRow } from "@/components/LeaderboardCard";
import styles from "./players.module.css";

const STAT_ORDER = ["goals", "goal_assist", "expected_goals", "expected_goalsontarget", "rating"];

const INTEGER_STATS = new Set(["goals", "goal_assist"]);

function formatValue(statKey: string, value: number): string {
  return INTEGER_STATS.has(statKey) ? String(Math.round(value)) : value.toFixed(2);
}

export default async function PlayersPage({
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
        <LeagueNav leagueId={id} active="players" season={seasonParam} />
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

  const statKeys = STAT_ORDER.filter((k) => data.player_leaderboards[k]);

  return (
    <main className={styles.page}>
      <LeagueNav leagueId={id} active="players" season={data.season} />
      <div className={styles.header}>
        <h1 className={styles.title}>英超 · 球员数据榜</h1>
        <span className={styles.seasonChip}>{data.season}</span>
      </div>

      <div className={styles.grid}>
        {statKeys.map((statKey) => {
          const board = data.player_leaderboards[statKey];
          const rows: LeaderboardRow[] = board.entries.map((e) => ({
            rank: e.rank,
            name: e.player_name_zh_short ?? e.player_name_zh ?? e.Player_Name ?? e.player_id,
            subtitle: e.team_name_zh ?? e.Team_Name,
            value: formatValue(statKey, e.value),
          }));
          return <LeaderboardCard key={statKey} title={board.label_zh} rows={rows} />;
        })}
      </div>
    </main>
  );
}
