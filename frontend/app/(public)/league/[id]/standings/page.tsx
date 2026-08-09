import { fetchLeagueNameZh } from "@/lib/api";
import { leagueSectionPath, serverGetOptional, type StandingsResponse } from "@/lib/api-v1";
import { LeagueNav } from "@/components/LeagueNav";
import { StandingsTable } from "@/components/league/StandingsTable";
import { MemberLeagueSection } from "@/components/league/MemberLeagueSection";
import { SeasonSwitcher } from "@/components/league/SeasonSwitcher";
import styles from "./standings.module.css";

// 已迁移到 /api/v1/leagues/{id}/standings(联赛门禁 + TeamRef 中文名解析在服务端):
// - 免费联赛(英超等):服务端匿名取数直接 SSR,公共 HTML 不因登录态变化;
// - Pro 联赛(西甲/德甲/意甲/法甲):匿名取数被 401/403 挡下 → 渲染客户端
//   会员加载器,浏览器带会话 cookie 重取;无权益时显示会员引导,不再渲染空表格。
export default async function StandingsPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ season?: string }>;
}) {
  const { id } = await params;
  const { season: seasonParam } = await searchParams;

  let data: StandingsResponse | null;
  try {
    data = await serverGetOptional<StandingsResponse>(
      leagueSectionPath("standings", id, seasonParam)
    );
  } catch {
    return (
      <main className={styles.page}>
        <LeagueNav leagueId={id} active="standings" season={seasonParam} />
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
      <LeagueNav leagueId={id} active="standings" season={seasonParam} />
      <div className={styles.header}>
        <h1 className={styles.title}>{leagueNameZh} · 排名榜</h1>
      </div>
      {data && (
        <SeasonSwitcher
          leagueId={id}
          section="standings"
          seasons={data.available_seasons}
          selected={seasonParam}
          resolved={resolvedSeason}
        />
      )}

      {data ? (
        <StandingsTable rows={data.rows} />
      ) : (
        <MemberLeagueSection kind="standings" leagueId={id} season={seasonParam} />
      )}
    </main>
  );
}
