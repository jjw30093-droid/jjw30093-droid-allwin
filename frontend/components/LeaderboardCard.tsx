import styles from "./LeaderboardCard.module.css";

export interface LeaderboardRow {
  rank: number;
  name: string;
  subtitle?: string | null;
  value: string;
}

export function LeaderboardCard({
  title,
  rows,
}: {
  title: string;
  rows: LeaderboardRow[];
}) {
  return (
    <div className={styles.card}>
      <div className={styles.title}>{title}</div>
      <ol className={styles.list}>
        {rows.map((r) => (
          <li key={r.rank} className={styles.row}>
            <span className={styles.rank}>{r.rank}</span>
            <span className={styles.name}>
              {r.name}
              {r.subtitle && <span className={styles.subtitle}> · {r.subtitle}</span>}
            </span>
            <span className={styles.value}>{r.value}</span>
          </li>
        ))}
        {rows.length === 0 && <li className={styles.empty}>暂无数据</li>}
      </ol>
    </div>
  );
}
