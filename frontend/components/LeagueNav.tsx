import Link from "next/link";
import styles from "./LeagueNav.module.css";

const TABS = [
  // 速览排第一:四张图(进球时段/常见比分/大小球/主平客)全部来自银层表,
  // 是本页面组里唯一图形化、也是最不需要背景知识就能看懂的一档。
  { key: "overview", label: "速览", path: "overview" },
  { key: "standings", label: "排名", path: "standings" },
  { key: "matches", label: "赛程", path: "matches" },
  { key: "team-stats", label: "球队数据", path: "team-stats" },
  { key: "players", label: "球员榜", path: "players" },
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
