/** 联赛页赛季链接(纯函数,和 lib/match-filters.ts 的 buildMatchesHref 同范式)。
 * section 对应 components/LeagueNav.tsx 的 TABS[].path(如 "standings"/"matches"),
 * 不是 API 端点的 kind("fixtures")——两者在"赛程"这一项上刻意不同。
 * 赛季串含斜杠("2025/2026"),沿用 LeagueNav 已有约定显式编码。 */
export function buildLeagueSeasonHref(
  leagueId: string,
  section: string,
  season?: string,
): string {
  const base = `/league/${leagueId}/${section}`;
  return season ? `${base}?season=${encodeURIComponent(season)}` : base;
}
