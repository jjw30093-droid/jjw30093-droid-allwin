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

/**
 * 详情页"上一场/下一场"导航的取数 query 串(2026-08-19 性能修复收口)。
 *
 * 此前 app/matches/[matchId]/page.tsx(SSR)与 MemberMatchDetail.tsx(客户端
 * 兜底)各自内联拼了一遍同一个 URL、各带 limit=200——整页 200 场比赛的
 * payload 最终只产出两个 match_id(上一场/下一场),两处拼接一旦分叉会造成
 * SSR 与浏览器给出不同的导航结果。收口到这一个函数,SSR 与客户端共用
 * (与 frontend/lib/match-filters.ts::buildMatchesApiQuery 同一条纪律)。
 *
 * limit 从 200 降到 40:不新建后端接口去精确算"紧邻的两场"——那需要复刻
 * backend/queries/matches.py::list_matches 的排序语义(status=upcoming 不是
 * 纯按开球时间,已发布分析/赔率的比赛会被提权到前面,"下一场"点进去的其实
 * 是"列表页排序下的下一场",不是"开球时间上最近的下一场"),贸然简化会
 * 悄悄改变用户点"下一场"实际跳到哪一场。40 场足够覆盖"同一个联赛一周内"
 * 的正常赛程密度(五大联赛单轮同时开赛也不到 40 场同周),仍然比 200 小
 * 5 倍,且 status/window/排序完全沿用既有 /api/v1/matches 契约不变。
 */
const RELATED_MATCHES_LIMIT = 40;

export function relatedMatchesQuery(leagueId: number): string {
  const qs = new URLSearchParams();
  qs.set("league_id", String(leagueId));
  qs.set("status", "upcoming");
  qs.set("window", "7d");
  qs.set("limit", String(RELATED_MATCHES_LIMIT));
  return qs.toString();
}

/** 返回链接文案按来源区分,避免从联赛页进来还写着"筛选结果"。 */
export function returnLabelFor(returnTo: string): string {
  if (returnTo.startsWith("/league/")) return "返回赛程";
  if (returnTo.startsWith("/track-record")) return "返回公开战绩";
  if (returnTo === "/") return "返回首页";
  return "返回当前筛选结果";
}
