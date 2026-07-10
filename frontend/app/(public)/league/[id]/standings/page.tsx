import { fetchLeagueOverview, type StandingRow } from "@/lib/api";
import { LeagueNav } from "@/components/LeagueNav";
import styles from "./standings.module.css";

const QUAL_LABELS: Record<string, string> = {
  "#2AD572": "欧冠资格",
  "#0046A7": "欧联资格",
  "#02CCF0": "欧协联附加",
  "#FF4646": "降级区",
};

function GoalDiff({ value }: { value: number }) {
  const sign = value > 0 ? "+" : "";
  const cls = value > 0 ? styles.gdPos : value < 0 ? styles.gdNeg : "";
  return (
    <span className={`${styles.num} ${cls}`}>
      {sign}
      {value}
    </span>
  );
}

function Row({ row, isChampion }: { row: StandingRow; isChampion: boolean }) {
  return (
    <tr className={isChampion ? styles.championRow : undefined}>
      <td className={styles.rank}>
        {row.qual_color && (
          <span
            className={styles.qualStripe}
            style={{ background: row.qual_color }}
          />
        )}
        <span className={styles.num}>{row.position}</span>
      </td>
      <td className={styles.team}>
        <span className={styles.teamNameZh}>
          {row.team_name_zh ?? `Team ${row.team_id}`}
        </span>
        {isChampion && (
          <span
            style={{
              marginLeft: 8,
              fontSize: 11,
              color: "#2A1B06",
              background: "var(--gold-grad)",
              borderRadius: "var(--r-pill)",
              padding: "2px 8px",
              fontWeight: 700,
            }}
          >
            冠军
          </span>
        )}
      </td>
      <td className={styles.num}>{row.played}</td>
      <td className={`${styles.num} ${styles.wdl}`}>{row.wins}</td>
      <td className={`${styles.num} ${styles.wdl}`}>{row.draws}</td>
      <td className={`${styles.num} ${styles.wdl}`}>{row.losses}</td>
      <td className={`${styles.num} ${styles.gf}`}>{row.goals_for}</td>
      <td className={`${styles.num} ${styles.ga}`}>{row.goals_against}</td>
      <td>
        <GoalDiff value={row.goal_diff} />
      </td>
      <td className={`${styles.num} ${styles.points}`}>{row.points}</td>
    </tr>
  );
}

export default async function StandingsPage({
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
        <LeagueNav leagueId={id} active="standings" season={seasonParam} />
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

  const usedQualColors = Array.from(
    new Set(data.standings.map((r) => r.qual_color).filter(Boolean))
  ) as string[];

  return (
    <main className={styles.page}>
      <LeagueNav leagueId={id} active="standings" season={data.season} />
      <div className={styles.header}>
        <h1 className={styles.title}>英超 · 排名榜</h1>
        <span className={styles.seasonChip}>{data.season}</span>
      </div>

      <div className={styles.card}>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>名次</th>
                <th>球队</th>
                <th>场次</th>
                <th className={styles.wdl}>胜</th>
                <th className={styles.wdl}>平</th>
                <th className={styles.wdl}>负</th>
                <th>进球</th>
                <th>失球</th>
                <th>净胜</th>
                <th>积分</th>
              </tr>
            </thead>
            <tbody>
              {data.standings.map((row) => (
                <Row
                  key={row.team_id}
                  row={row}
                  isChampion={row.position === 1}
                />
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {usedQualColors.length > 0 && (
        <div className={styles.legend}>
          {usedQualColors.map((color) => (
            <span key={color}>
              <span
                className={styles.legendDot}
                style={{ background: color }}
              />
              {QUAL_LABELS[color] ?? color}
            </span>
          ))}
        </div>
      )}
    </main>
  );
}
