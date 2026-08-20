import type { Metadata } from "next";
import Link from "next/link";
import { LocalTime } from "@/components/matches/LocalTime";
import { serverGet, type LeagueInfo } from "@/lib/api-v1";
import styles from "./leagues.module.css";

export const metadata: Metadata = {
  title: "联赛数据",
  description: "按真实数据可用状态浏览联赛排名、赛程与球队数据。",
};

function priority(league: LeagueInfo): number {
  return league.data_status === "AVAILABLE" ? 0 : 1;
}

export default async function LeaguesPage() {
  let leagues: LeagueInfo[];
  try {
    leagues = await serverGet<LeagueInfo[]>("/api/v1/leagues", {
      revalidate: 60,
    });
  } catch {
    return (
      <main className={styles.page}>
        <h1>联赛数据</h1>
        <div className={styles.errorBox}>
          数据暂时无法加载，请稍后刷新重试。
        </div>
      </main>
    );
  }

  const ordered = [...leagues].sort(
    (a, b) => priority(a) - priority(b) || a.name_zh.localeCompare(b.name_zh, "zh-CN"),
  );

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>真实数据覆盖</p>
          <h1>联赛数据</h1>
          <p>优先展示已经同步真实数据的联赛。</p>
        </div>
        <Link href="/matches?status=upcoming&window=7d" className={styles.matchLink}>
          查看未来七天比赛 →
        </Link>
      </header>

      <div className={styles.grid}>
        {ordered.map((league) => {
          const available = league.data_status === "AVAILABLE";
          return (
            <article className={styles.card} key={league.league_id}>
              <div className={styles.cardHead}>
                <div>
                  <h2>{league.name_zh}</h2>
                  <p>{league.name_en}</p>
                </div>
                <span
                  className={styles.status}
                  data-status={available ? "available" : "empty"}
                >
                  {available ? "已有真实数据" : "暂未同步"}
                </span>
              </div>

              <dl className={styles.meta}>
                <div>
                  <dt>当前赛季</dt>
                  <dd>{league.current_season ?? "待同步"}</dd>
                </div>
                <div>
                  <dt>最近更新</dt>
                  <dd>
                    {league.data_updated_at ? (
                      <LocalTime iso={league.data_updated_at} />
                    ) : (
                      "暂无可信时间"
                    )}
                  </dd>
                </div>
              </dl>

              <div className={styles.actions}>
                {available ? (
                  // 所有联赛对匿名同等可访问,不再有登录门禁区分。
                  <>
                    <Link href={`/league/${league.league_id}/standings`}>排名</Link>
                    <Link href={`/league/${league.league_id}/matches`}>赛程</Link>
                    <Link href={`/league/${league.league_id}/team-stats`}>球队数据</Link>
                  </>
                ) : (
                  <span>该联赛数据尚未同步</span>
                )}
              </div>
            </article>
          );
        })}
      </div>
    </main>
  );
}
