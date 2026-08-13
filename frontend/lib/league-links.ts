/** 联赛页赛季链接(纯函数,和 lib/match-filters.ts 的 buildMatchesHref 同范式)。
 * section 对应 components/LeagueNav.tsx 的 TABS[].path(如 "standings"/"matches"),
 * 不是 API 端点的 kind("fixtures")——两者在"赛程"这一项上刻意不同。
 * 赛季串含斜杠("2025/2026"),沿用 LeagueNav 已有约定显式编码。 */
export function buildLeagueSeasonHref(
  leagueId: string,
  section: string,
  season?: string,
  /** 排名页专用:总榜/主场/客场/近期/xG 榜。缺省(all)不写进 URL。 */
  tableType?: string,
): string {
  const base = `/league/${leagueId}/${section}`;
  const params = new URLSearchParams();
  if (season) params.set("season", season);
  if (tableType && tableType !== "all") params.set("table_type", tableType);
  const qs = params.toString();
  return qs ? `${base}?${qs}` : base;
}
