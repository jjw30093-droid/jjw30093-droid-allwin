import type { Metadata } from "next";
import Link from "next/link";
import { LocalTime } from "@/components/matches/LocalTime";
import { serverGet, type LeagueInfo } from "@/lib/api-v1";
import styles from "./leagues.module.css";

export const metadata: Metadata = {
  title: "联赛数据 — 欧赢 ALLWIN",
  description: "按真实数据可用状态浏览联赛排名、赛程与球队数据。",
};

function priority(league: LeagueInfo): number {
  if (league.data_status === "AVAILABLE" && league.accessible) return 0;
  if (league.data_status === "AVAILABLE") return 1;
  return 2;
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
          <p>优先展示已经同步且当前可访问的联赛。</p>
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
                  <dt>访问权限</dt>
                  <dd>
                    {league.accessible
                      ? "当前可访问"
                      : league.requires_login
                        ? "登录后免费查看"
                        : "需要登录"}
                  </dd>
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
                  // 有数据即给入口(审计 B4):此前 `accessible` 参与决定链接存废,
                  // 而它来自匿名 SSR 取数——已登录用户也看不到任何指向受限
                  // 联赛页面的链接。目标页自带门禁引导(MemberLeagueSection),
                  // 链接过去对任何身份都是安全的;accessible 只决定是否附带
                  // 登录提示(登录即免费解锁,不存在付费联赛)。
                  <>
                    <Link href={`/league/${league.league_id}/standings`}>排名</Link>
                    <Link href={`/league/${league.league_id}/matches`}>赛程</Link>
                    <Link href={`/league/${league.league_id}/team-stats`}>球队数据</Link>
                    {!league.accessible && (
                      <Link
                        href={`/login?next=/league/${league.league_id}/standings`}
                        className={styles.proHint}
                      >
                        登录后免费查看
                      </Link>
                    )}
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
