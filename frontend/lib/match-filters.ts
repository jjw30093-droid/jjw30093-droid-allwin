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

/**
 * 构造 `/api/v1/matches` 的请求query串 —— 服务端 SSR(匿名口径)与浏览器端
 * 会员刷新(见 MatchListLive)共用同一份映射,不允许出现第二套各自维护、
 * 迟早漂移的参数拼接逻辑。
 */
export function buildMatchesApiQuery(
  filters: MatchFilters,
  opts: { limit: number; windowOverride?: MatchFilters["window"] },
): string {
  const { date, league, season, status, content, q, page } = filters;
  const window = opts.windowOverride ?? filters.window;
  const qs = new URLSearchParams();
  if (date) qs.set("date", date);
  if (league != null) qs.set("league_id", String(league));
  if (season) qs.set("season", season);
  if (status !== "all") qs.set("status", status);
  qs.set("window", window);
  if (content) qs.set("content", content);
  if (q) qs.set("q", q);
  qs.set("limit", String(opts.limit));
  qs.set("offset", String((page - 1) * opts.limit));
  return qs.toString();
}

/**
 * 默认视图(status=upcoming 且用户未显式指定 window/date/season/q)在赛季
 * 间歇期会是 0 场——这个条件决定"要不要自动放宽到全部未来赛程"。SSR 与
 * 浏览器端刷新必须用同一个判据,否则会员刷新后的放宽时机会和匿名首屏不一致。
 */
export function isWindowAutoWidenEligible(
  filters: Pick<MatchFilters, "date" | "season" | "q" | "status">,
  windowExplicit: boolean,
): boolean {
  return (
    filters.status === "upcoming" &&
    !windowExplicit &&
    !filters.date &&
    !filters.season &&
    !filters.q
  );
}
