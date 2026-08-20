import type { Metadata } from "next";
import { fetchLeagueNameZh } from "@/lib/api";
import { leagueSectionMetadata } from "@/lib/league-metadata";
import { leagueSectionPath, serverGetOptional, type GetJson } from "@/lib/api-v1";
import { LeagueNav } from "@/components/LeagueNav";
import { SeasonProfileCharts } from "@/components/league/SeasonProfileCharts";
import { MemberLeagueSection } from "@/components/league/MemberLeagueSection";
import { SeasonSwitcher } from "@/components/league/SeasonSwitcher";
import styles from "./overview.module.css";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<Metadata> {
  const { id } = await params;
  return leagueSectionMetadata(id, "赛季速览", "赛季进度、大小球分布与进球时间分桶等数据概况。");
}

type Profile = GetJson<"/api/v1/leagues/{league_id}/season-profile">;

/**
 * 联赛速览:四张图消费 silver_goal_minute_buckets / silver_score_distribution /
 * silver_over_under_thresholds / silver_league_season_summary —— 这四张银层表
 * 早就构建完成(共 1,884 行),但此前**前端零消费**,三个公开联赛页的
 * EChart import 数是 0。
 *
 * 与其它联赛页同一取数模式:匿名 SSR 直取;被联赛门禁挡下时(需登录联赛)
 * 渲染客户端会员加载器,不留空壳。
 */
export default async function LeagueOverviewPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ season?: string }>;
}) {
  const { id } = await params;
  const { season: seasonParam } = await searchParams;

  let data: Profile | null;
  try {
    data = await serverGetOptional<Profile>(
      leagueSectionPath("season-profile", id, seasonParam),
    );
  } catch {
    return (
      <main className={styles.page}>
        <LeagueNav leagueId={id} active="overview" season={seasonParam} />
        <div className={styles.errorBox}>
          <div className={styles.errorTitle}>数据暂时无法加载</div>
          <p>该联赛数据尚未同步，或数据服务暂时不可用。请稍后再试。</p>
        </div>
      </main>
    );
  }

  const leagueNameZh = await fetchLeagueNameZh(id);
  const resolvedSeason = data?.season ?? seasonParam;

  return (
    <main className={styles.page}>
      <LeagueNav leagueId={id} active="overview" season={seasonParam} />
      <div className={styles.header}>
        <h1 className={styles.title}>{leagueNameZh} · 赛季速览</h1>
      </div>
      {data && (
        <SeasonSwitcher
          leagueId={id}
          section="overview"
          seasons={data.available_seasons}
          selected={seasonParam}
          resolved={resolvedSeason}
        />
      )}

      {data ? (
        <SeasonProfileCharts profile={data} />
      ) : (
        <MemberLeagueSection kind="standings" leagueId={id} season={seasonParam} />
      )}
    </main>
  );
}
