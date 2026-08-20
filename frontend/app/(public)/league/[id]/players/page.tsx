import type { Metadata } from "next";
import { fetchLeagueNameZh } from "@/lib/api";
import { leagueSectionMetadata } from "@/lib/league-metadata";
import { leagueSectionPath, serverGetOptional, type PlayersResponse } from "@/lib/api-v1";
import { LeagueNav } from "@/components/LeagueNav";
import { PlayerBoards } from "@/components/league/PlayerBoards";
import { MemberLeagueSection } from "@/components/league/MemberLeagueSection";
import { SeasonSwitcher } from "@/components/league/SeasonSwitcher";
import styles from "./players.module.css";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<Metadata> {
  const { id } = await params;
  return leagueSectionMetadata(id, "球员数据榜", "射手榜、助攻榜等球员数据排行。");
}

// 已迁移到 /api/v1/leagues/{id}/players(2026-08-16 起对任何人恒含全部维度,
// 服务端完成中文名解析与 top10 截取)。
// 服务端取数失败/未同步时走客户端加载器重新请求一次(见 standings 页同款说明)。
export default async function PlayersPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ season?: string }>;
}) {
  const { id } = await params;
  const { season: seasonParam } = await searchParams;

  let data: PlayersResponse | null;
  try {
    data = await serverGetOptional<PlayersResponse>(
      leagueSectionPath("players", id, seasonParam)
    );
  } catch {
    return (
      <main className={styles.page}>
        <LeagueNav leagueId={id} active="players" season={seasonParam} />
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
      <LeagueNav leagueId={id} active="players" season={seasonParam} />
      <div className={styles.header}>
        <h1 className={styles.title}>{leagueNameZh} · 球员数据榜</h1>
      </div>
      {data && (
        <SeasonSwitcher
          leagueId={id}
          section="players"
          seasons={data.available_seasons}
          selected={seasonParam}
          resolved={resolvedSeason}
        />
      )}

      {data ? (
        <PlayerBoards boards={data.boards} />
      ) : (
        <MemberLeagueSection kind="players" leagueId={id} season={seasonParam} />
      )}
    </main>
  );
}
