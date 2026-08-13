import { fetchLeagueNameZh } from "@/lib/api";
import { leagueSectionPath, serverGetOptional, type TeamStatsResponse } from "@/lib/api-v1";
import { LeagueNav } from "@/components/LeagueNav";
import { TeamStatsBoards } from "@/components/league/TeamStatsBoards";
import { TeamQuadrantChart } from "@/components/league/TeamQuadrantChart";
import { MemberLeagueSection } from "@/components/league/MemberLeagueSection";
import { SeasonSwitcher } from "@/components/league/SeasonSwitcher";
import styles from "./team-stats.module.css";

// 已迁移到 /api/v1/leagues/{id}/team-stats(免费字段投影:射门/射正/控球/xG/xGOT;
// 角球/红黄牌/零封/BTTS 是付费深度字段,响应里物理不存在,这里也没有)。
// Pro 联赛的匿名请求被门禁挡下时走客户端会员加载器(见 standings 页同款说明)。
export default async function TeamStatsPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ season?: string }>;
}) {
  const { id } = await params;
  const { season: seasonParam } = await searchParams;

  let data: TeamStatsResponse | null;
  try {
    data = await serverGetOptional<TeamStatsResponse>(
      leagueSectionPath("team-stats", id, seasonParam)
    );
  } catch {
    return (
      <main className={styles.page}>
        <LeagueNav leagueId={id} active="team-stats" season={seasonParam} />
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
      <LeagueNav leagueId={id} active="team-stats" season={seasonParam} />
      <div className={styles.header}>
        <h1 className={styles.title}>{leagueNameZh} · 球队数据榜</h1>
      </div>
      {data && (
        <SeasonSwitcher
          leagueId={id}
          section="team-stats"
          seasons={data.available_seasons}
          selected={seasonParam}
          resolved={resolvedSeason}
        />
      )}

      {data ? (
        <>
          {/* 象限图在前:一屏看到全部球队,补上 top10 榜里第 11–20 名的空白 */}
          <TeamQuadrantChart rows={data.rows} />
          <TeamStatsBoards rows={data.rows} />
        </>
      ) : (
        <MemberLeagueSection kind="team-stats" leagueId={id} season={seasonParam} />
      )}
    </main>
  );
}
