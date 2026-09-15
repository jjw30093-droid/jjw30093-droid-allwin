/** 联赛页赛季链接(纯函数,和 lib/match-filters.ts 的 buildMatchesHref 同范式)。
 * section 对应 components/LeagueNav.tsx 的 TABS[].path(如 "standings"/"matches"),
 * 不是 API 端点的 kind("fixtures")——两者在"赛程"这一项上刻意不同。
 * 赛季串含斜杠("2025/2026"),沿用 LeagueNav 已有约定显式编码。
 *
 * 2026-09-14 起第三参改选项对象(此前是 season/tableType 两个位置参数,
 * 球队数据页"最近 N 场/主客场"筛选又要加两个,四个位置参数堆在一起容易
 * 传错顺序):recency/venue 只有 team-stats 页的 RecencyVenueSwitcher 用。 */
export function buildLeagueSeasonHref(
  leagueId: string,
  section: string,
  opts?: {
    season?: string;
    /** 排名页专用:总榜/主场/客场/近期/xG 榜。缺省(all)不写进 URL。 */
    tableType?: string;
    /** 球队数据页专用:最近 3/5/10 场。缺省(不筛)不写进 URL。 */
    recency?: number;
    /** 球队数据页专用:主场/客场/全部。缺省(all)不写进 URL。 */
    venue?: string;
  },
): string {
  const { season, tableType, recency, venue } = opts ?? {};
  const base = `/league/${leagueId}/${section}`;
  const params = new URLSearchParams();
  if (season) params.set("season", season);
  if (tableType && tableType !== "all") params.set("table_type", tableType);
  if (recency) params.set("recency", String(recency));
  if (venue && venue !== "all") params.set("venue", venue);
  const qs = params.toString();
  return qs ? `${base}?${qs}` : base;
}
