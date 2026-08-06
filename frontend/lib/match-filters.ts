export interface MatchFilters {
  date?: string;
  league?: number;
  /** 赛季,如 "2024/2025";自然年赛季联赛(挪超/瑞超)为 "2026"。 */
  season?: string;
  status: "upcoming" | "finished" | "all";
  window: "today" | "tomorrow" | "3d" | "7d" | "all";
  content?: "analysis" | "odds";
  q?: string;
  page: number;
}

/** Build shareable match-list URLs without dropping any active filter. */
export function buildMatchesHref(
  filters: MatchFilters,
  patch: Partial<MatchFilters>,
): string {
  const next = { ...filters, ...patch };
  const query = new URLSearchParams();
  if (next.date) query.set("date", next.date);
  if (next.league != null) query.set("league", String(next.league));
  if (next.season) query.set("season", next.season);
  if (next.status !== "upcoming") query.set("status", next.status);
  if (next.window !== "7d") query.set("window", next.window);
  if (next.content) query.set("content", next.content);
  if (next.q) query.set("q", next.q);
  if (next.page > 1) query.set("page", String(next.page));
  const value = query.toString();
  return value ? `/matches?${value}` : "/matches";
}
