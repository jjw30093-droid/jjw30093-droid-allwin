import { fetchLeagueMatches, type MatchRow } from "@/lib/api";
import { LeagueNav } from "@/components/LeagueNav";
import styles from "./matches.module.css";

// 赛程/结果是免费层,只展示对阵/比分/日期/轮次/状态——概率/预测在付费
// 概率卡页(wdl-predictions),这里完全不引入,也不该引入。
function MatchRowView({ m }: { m: MatchRow }) {
  const isFinished = m.status === "Finish";
  return (
    <div className={styles.matchRow}>
      <span className={styles.homeTeam}>{m.home_team_name_zh ?? m.home_team_id}</span>
      <div className={styles.center}>
        {isFinished ? (
          <span className={styles.score}>
            {m.home_score} - {m.away_score}
          </span>
        ) : (
          <>
            <span className={styles.kickoffDate}>{m.Date}</span>
            <span className={styles.statusBadge}>未开赛</span>
          </>
        )}
      </div>
      <span className={styles.awayTeam}>{m.away_team_name_zh ?? m.away_team_id}</span>
    </div>
  );
}

export default async function MatchesPage({
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
    data = await fetchLeagueMatches(id, seasonParam);
  } catch (err) {
    return (
      <main className={styles.page}>
        <LeagueNav leagueId={id} active="matches" season={seasonParam} />
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

  const rounds = Array.from(new Set(data.matches.map((m) => m.Match_Round))).sort(
    (a, b) => Number(a) - Number(b)
  );

  return (
    <main className={styles.page}>
      <LeagueNav leagueId={id} active="matches" season={data.season} />
      <div className={styles.header}>
        <h1 className={styles.title}>英超 · 赛程与结果</h1>
        <span className={styles.seasonChip}>{data.season}</span>
      </div>

      {rounds.map((round) => (
        <div key={round}>
          <div className={styles.roundHeading}>第 {round} 轮</div>
          <div className={styles.card}>
            {data.matches
              .filter((m) => m.Match_Round === round)
              .map((m) => (
                <MatchRowView key={m.Match_ID} m={m} />
              ))}
          </div>
        </div>
      ))}
    </main>
  );
}
