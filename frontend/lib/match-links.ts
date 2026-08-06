/** 比赛详情链接与返回上下文(纯函数,和 lib/league-links.ts 同范式)。
 *
 * 此前 `/matches/${id}?from=` 在 6 处内联拼接,且详情页的 returnTo 白名单
 * 只认 "/matches" 开头——来自联赛赛程页/公开战绩页的返回上下文会被静默丢成
 * "/matches"(审计 B5 的一部分)。统一收口到这里。
 */

/** 构造比赛详情 href;returnTo 为发起页的站内路径(含查询串)。 */
export function buildMatchHref(matchId: number, returnTo?: string): string {
  const base = `/matches/${matchId}`;
  return returnTo ? `${base}?from=${encodeURIComponent(returnTo)}` : base;
}

/** 允许作为返回目标的站内路径前缀(均为真实存在的列表型页面)。
 * 注意首页 "/" 只允许精确匹配,不能作为前缀——否则任何以 "/" 开头的路径
 * (即所有路径)都会被放行,白名单形同虚设。 */
const RETURN_PREFIXES = ["/matches", "/league/", "/track-record"] as const;

/**
 * 校验 from 查询参数:只接受站内相对路径,拒绝开放重定向
 * ("//host"、含协议的 "https:"、反斜杠变体),不合法回退 "/matches"。
 */
export function sanitizeReturnTo(from: string | undefined): string {
  if (!from) return "/matches";
  if (from.startsWith("//") || from.includes(":") || from.includes("\\")) {
    return "/matches";
  }
  if (from === "/" || RETURN_PREFIXES.some((p) => from === p || from.startsWith(p))) {
    return from;
  }
  return "/matches";
}

/** 返回链接文案按来源区分,避免从联赛页进来还写着"筛选结果"。 */
export function returnLabelFor(returnTo: string): string {
  if (returnTo.startsWith("/league/")) return "返回赛程";
  if (returnTo.startsWith("/track-record")) return "返回公开战绩";
  if (returnTo === "/") return "返回首页";
  return "返回当前筛选结果";
}
