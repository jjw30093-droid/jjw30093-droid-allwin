export type MatchStatusFilter = "upcoming" | "finished" | "all";

/** 时间窗 token。向未来与向过去一一对称,语义真源在
 * backend/queries/matches.py::_window_bounds:
 * - 自然日档(北京时间)yesterday / today / tomorrow —— today 天然双向,
 *   「今天赛果」= today + status=finished,不需要另造 token;
 * - 滚动档 past7d/past3d = [now-N, now) 与 3d/7d = [now, now+N) 在 now 处互补。 */
export type MatchWindowFilter =
  | "yesterday"
  | "today"
  | "tomorrow"
  | "past3d"
  | "past7d"
  | "3d"
  | "7d"
  | "all";

export interface MatchFilters {
  date?: string;
  league?: number;
  /** 赛季,如 "2024/2025";自然年赛季联赛(挪超/瑞超)为 "2026"。 */
  season?: string;
  status: MatchStatusFilter;
  window: MatchWindowFilter;
  content?: "analysis" | "odds";
  q?: string;
  page: number;
}

/**
 * 该状态下的默认时间窗。**URL 省略规则(buildMatchesHref)与解析补默认值
 * (app/matches/page.tsx)必须共用这一个函数**——两处各写一份的话,用户在
 * 赛果视图点联赛/内容/翻页 chip 就会被静默换掉窗口,而 URL 看起来完全正常,
 * 线上无声、肉眼查不出来。
 *
 * 赛果默认取 `all` 而不是 `past7d`,三条理由:
 * 1. 永不空页——赛果按开球时间倒序,第 1 页天然就是"最近的赛果",正是用户
 *    点「赛果」想要的;而 past7d 在赛季间歇期会真的是 0 场;
 * 2. 旧链接 /matches?status=finished&window=all(现有已完赛 chip、e2e、可能
 *    已被收藏或抓取)解析结果与新默认完全一致,行为零变化;
 * 3. /matches?status=finished(不带 window)从"白板"变成完整赛果,只改善
 *    不回退。
 * past7d / past3d / yesterday 仍然作为时间 chip 提供,只是不当默认值。
 */
export function defaultWindowFor(status: MatchStatusFilter): MatchWindowFilter {
  return status === "upcoming" ? "7d" : "all";
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
  // 省略规则与解析端共用 defaultWindowFor:URL 里没有 window 时,解析端会按
  // status 补上同一个默认值,往返恒等。写死 "7d" 会让赛果链接省略掉 window,
  // 而解析端补出 "all"——不一致的那一刻就是一个看不出问题的空白页。
  if (next.window !== defaultWindowFor(next.status)) query.set("window", next.window);
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
  // date 已经把范围钉死到一天(后端按 dim_match.Date 精确匹配),再 AND 一个
  // 向未来的时间窗只会制造恒空:真实缺陷是 /matches?date=<过去某天> 渲染出
  // 白板,而那天其实有几十场已完赛比赛——日期表单的 hidden 字段把
  // status=upcoming 与 window=7d 一起回传,三者在 SQL 里是 AND。
  // windowOverride(自动放宽路径)优先级仍然最高。
  const window = opts.windowOverride ?? (date ? "all" : filters.window);
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
