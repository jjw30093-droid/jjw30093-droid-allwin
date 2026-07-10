import Link from "next/link";
import styles from "./LeagueNav.module.css";

const TABS = [
  { key: "standings", label: "排名", path: "standings" },
  { key: "matches", label: "赛程", path: "matches" },
  { key: "team-stats", label: "球队数据", path: "team-stats" },
  { key: "players", label: "球员榜", path: "players" },
  { key: "wdl-predictions", label: "概率卡", path: "wdl-predictions" },
] as const;

export function LeagueNav({
  leagueId,
  active,
  season,
}: {
  leagueId: string;
  active: (typeof TABS)[number]["key"];
  season?: string;
}) {
  return (
    <nav className={styles.nav}>
      {TABS.map((t) => {
        const href = season
          ? `/league/${leagueId}/${t.path}?season=${encodeURIComponent(season)}`
          : `/league/${leagueId}/${t.path}`;
        return (
          <Link
            key={t.key}
            href={href}
            className={t.key === active ? styles.active : styles.link}
          >
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}
