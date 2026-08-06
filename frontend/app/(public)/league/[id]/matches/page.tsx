import { fetchLeagueNameZh } from "@/lib/api";
import { leagueSectionPath, serverGetOptional, type LeagueFixturesResponse } from "@/lib/api-v1";
import { LeagueNav } from "@/components/LeagueNav";
import { FixtureRounds } from "@/components/league/FixtureRounds";
import { MemberLeagueSection } from "@/components/league/MemberLeagueSection";
import { SeasonSwitcher } from "@/components/league/SeasonSwitcher";
import { buildLeagueSeasonHref } from "@/lib/league-links";
import styles from "./matches.module.css";

// 已迁移到 /api/v1/leagues/{id}/fixtures。赛程/结果是免费层展示形态,只有
// 对阵/比分/日期/轮次/状态——概率是付费概率卡页的事,这里没有、也不该有。
// Pro 联赛的匿名请求被门禁挡下时走客户端会员加载器(见 standings 页同款说明)。
export default async function MatchesPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ season?: string }>;
}) {
  const { id } = await params;
  const { season: seasonParam } = await searchParams;

  let data: LeagueFixturesResponse | null;
  try {
    data = await serverGetOptional<LeagueFixturesResponse>(
      leagueSectionPath("fixtures", id, seasonParam)
    );
  } catch {
    return (
      <main className={styles.page}>
        <LeagueNav leagueId={id} active="matches" season={seasonParam} />
        <div className={styles.errorBox}>
          <div className={styles.errorTitle}>数据暂时无法加载</div>
          <p>该联赛数据尚未同步，或数据服务暂时不可用。请稍后再试。</p>
        </div>
      </main>
    );
  }

  const leagueNameZh = await fetchLeagueNameZh(id);
  // resolvedSeason = 后端实际返回的赛季(徽章展示"在看什么");seasonParam = 用户
  // 显式选择(导航跨 tab 只带这个)——两者不能混用,否则一个 tab 的默认值会变成
  // 其它 tab 的显式选择,导致点导航跳到用户没选过的赛季(见 docs/data-plan.md)。
  const resolvedSeason = data?.season ?? seasonParam;

  return (
    <main className={styles.page}>
      <LeagueNav leagueId={id} active="matches" season={seasonParam} />
      <div className={styles.header}>
        <h1 className={styles.title}>{leagueNameZh} · 赛程与结果</h1>
      </div>
      {data && (
        <SeasonSwitcher
          leagueId={id}
          section="matches"
          seasons={data.available_seasons}
          selected={seasonParam}
          resolved={resolvedSeason}
        />
      )}

      {data ? (
        <FixtureRounds
          matches={data.matches}
          returnTo={buildLeagueSeasonHref(id, "matches", seasonParam)}
        />
      ) : (
        <MemberLeagueSection kind="fixtures" leagueId={id} season={seasonParam} />
      )}
    </main>
  );
}
