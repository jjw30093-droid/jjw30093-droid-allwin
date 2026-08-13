import { fetchLeagueNameZh } from "@/lib/api";
import { serverGetOptional, type StandingsResponse } from "@/lib/api-v1";
import { LeagueNav } from "@/components/LeagueNav";
import { StandingsTable } from "@/components/league/StandingsTable";
import { XgLuckChart } from "@/components/league/XgLuckChart";
import { MemberLeagueSection } from "@/components/league/MemberLeagueSection";
import { SeasonSwitcher } from "@/components/league/SeasonSwitcher";
import {
  TableTypeSwitcher,
  isTableType,
  type TableTypeKey,
} from "@/components/league/TableTypeSwitcher";
import styles from "./standings.module.css";

// 已迁移到 /api/v1/leagues/{id}/standings(联赛门禁 + TeamRef 中文名解析在服务端):
// - 免费联赛(英超等):服务端匿名取数直接 SSR,公共 HTML 不因登录态变化;
// - 需登录联赛:匿名取数被 401/403 挡下 → 渲染客户端会员加载器,浏览器带会话
//   cookie 重取;无权益时显示会员引导,不再渲染空表格。
//
// 2026-08-12:打开 table_type 五档。此前硬编码 'all',home/away/form/xg 共
// 2,892 行完全不可见;其中 xg 档带 x_points/x_position 与预算好的 xPointsDiff,
// 是"实际拿分 vs 按 xG 应得分"这类内容的唯一数据来源,单独用发散条形图渲染。
export default async function StandingsPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ season?: string; table_type?: string }>;
}) {
  const { id } = await params;
  const { season: seasonParam, table_type: tableTypeParam } = await searchParams;
  const tableType: TableTypeKey = isTableType(tableTypeParam)
    ? tableTypeParam
    : "all";

  const query = new URLSearchParams();
  if (seasonParam) query.set("season", seasonParam);
  if (tableType !== "all") query.set("table_type", tableType);
  const qs = query.toString();

  let data: StandingsResponse | null;
  try {
    data = await serverGetOptional<StandingsResponse>(
      `/api/v1/leagues/${id}/standings${qs ? `?${qs}` : ""}`,
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
  const heading = tableType === "xg" ? "xG 运气榜" : "排名榜";

  return (
    <main className={styles.page}>
      <LeagueNav leagueId={id} active="standings" season={seasonParam} />
      <div className={styles.header}>
        <h1 className={styles.title}>
          {leagueNameZh} · {heading}
        </h1>
      </div>
      <TableTypeSwitcher leagueId={id} season={seasonParam} active={tableType} />
      {data && (
        <SeasonSwitcher
          leagueId={id}
          section="standings"
          seasons={data.available_seasons}
          selected={seasonParam}
          resolved={resolvedSeason}
          tableType={tableType}
        />
      )}

      {data ? (
        tableType === "xg" ? (
          <XgLuckChart rows={data.rows} />
        ) : (
          <StandingsTable rows={data.rows} />
        )
      ) : (
        <MemberLeagueSection kind="standings" leagueId={id} season={seasonParam} />
      )}
    </main>
  );
}
