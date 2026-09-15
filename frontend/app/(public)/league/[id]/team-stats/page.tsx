import type { Metadata } from "next";
import { fetchLeagueNameZh } from "@/lib/api";
import { leagueSectionMetadata } from "@/lib/league-metadata";
import { leagueSectionPath, serverGetOptional, type TeamStatsResponse } from "@/lib/api-v1";
import { LeagueNav } from "@/components/LeagueNav";
import { TeamStatsSections } from "@/components/league/TeamStatsSections";
import { TeamQuadrantChart } from "@/components/league/TeamQuadrantChart";
import { MemberLeagueSection } from "@/components/league/MemberLeagueSection";
import { SeasonSwitcher } from "@/components/league/SeasonSwitcher";
import { RecencyVenueSwitcher } from "@/components/league/RecencyVenueSwitcher";
import seasonSwitcherStyles from "@/components/league/SeasonSwitcher.module.css";
import styles from "./team-stats.module.css";

/** ?recency= 只接受 3/5/10(与后端 RecencyWindow 枚举同一份白名单),
 *  其它值(包括打字打错、手改 URL)一律当作未筛选,不做静默取整或报错。 */
const RECENCY_VALUES = new Set([3, 5, 10]);
function parseRecency(raw: string | undefined): number | undefined {
  const n = raw ? Number(raw) : NaN;
  return RECENCY_VALUES.has(n) ? n : undefined;
}
const VENUE_VALUES = new Set(["home", "away", "all"]);
function parseVenue(raw: string | undefined): "home" | "away" | "all" | undefined {
  return raw && VENUE_VALUES.has(raw) ? (raw as "home" | "away" | "all") : undefined;
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<Metadata> {
  const { id } = await params;
  return leagueSectionMetadata(id, "球队数据榜", "各项球队数据排行:进攻、防守、控球与 xG。");
}

// 已迁移到 /api/v1/leagues/{id}/team-stats(免费字段投影:射门/射正/控球/xG/xGOT;
// 角球/红黄牌/零封/BTTS 是付费深度字段,响应里物理不存在,这里也没有)。
// Pro 联赛的匿名请求被门禁挡下时走客户端会员加载器(见 standings 页同款说明)。
export default async function TeamStatsPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ season?: string; recency?: string; venue?: string }>;
}) {
  const { id } = await params;
  const { season: seasonParam, recency: recencyParam, venue: venueParam } = await searchParams;
  const recency = parseRecency(recencyParam);
  const venue = parseVenue(venueParam);

  let data: TeamStatsResponse | null;
  try {
    data = await serverGetOptional<TeamStatsResponse>(
      leagueSectionPath("team-stats", id, { season: seasonParam, recency, venue })
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
      {/* 赛季 + 最近N场/主客场(2026-09-15 合并一行,站长要求:三行筛选控件
          手机上堆叠太占屏幕)。象限图 + 下方榜单随这三个维度整页联动,
          FotMob 来源方卡片(boards)筛选激活时后端已整段返回空数组,无需
          前端额外判断。 */}
      {data && (
        <div className={seasonSwitcherStyles.chipRow}>
          <SeasonSwitcher
            leagueId={id}
            section="team-stats"
            seasons={data.available_seasons}
            selected={seasonParam}
            resolved={resolvedSeason}
            recency={recency}
            venue={venue}
            inline
          />
          <RecencyVenueSwitcher leagueId={id} season={seasonParam} recency={recency} venue={venue} />
        </div>
      )}

      {data ? (
        data.rows.length === 0 ? (
          // 赛季样本不足(如刚开踢)或该联赛暂无数据,后端已给出具体理由
          // (backend/queries/league_stats.py::team_season_stats 的
          // MIN_MATCHES_FOR_SEASON_DATA 门槛,欧冠/欧联/欧协联除外)——
          // 整页(榜单 + 象限图)统一不展示,不画一张几乎全是单场波动的假榜。
          <p className={styles.empty}>
            {data.empty_reason ?? "该联赛暂无球队赛季统计数据。"}
          </p>
        ) : (
          <>
            {/* 象限图在前:一屏看到全部球队,补上 top10 榜里第 11–20 名的空白 */}
            <TeamQuadrantChart rows={data.rows} recency={recency} venue={venue} />
            <TeamStatsSections rows={data.rows} boards={data.boards ?? []} />
          </>
        )
      ) : (
        <MemberLeagueSection kind="team-stats" leagueId={id} season={seasonParam} />
      )}
    </main>
  );
}
