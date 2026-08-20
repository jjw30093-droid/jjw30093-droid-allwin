import type { Metadata } from "next";
import { fetchLeagueNameZh } from "@/lib/api";

/**
 * 联赛子页(/league/[id]/*)统一标题:与页面 h1 完全一致
 * (`{联赛中文名} · {栏目}`),站名后缀由根 layout 的 title.template 统一
 * 追加,各页不再手写。
 *
 * generateMetadata 跑在 page render **之前**——fetchLeagueNameZh 内部走
 * serverGet,非 2xx 会 throw,不 catch 的话 /api/v1/leagues 一挂就是整页
 * 500(现在"页面内错误框"级别的故障会被升级)。这里的 .catch(() => null)
 * 不是可选优化,是必需项(先例:app/matches/[matchId]/page.tsx 的
 * generateMetadata 同样 catch 取数失败)。
 */
export async function leagueSectionMetadata(
  leagueId: string,
  heading: string,
  description: string,
): Promise<Metadata> {
  const name = await fetchLeagueNameZh(leagueId).catch(() => null);
  return {
    title: name ? `${name} · ${heading}` : heading,
    description: name ? `${name}${description}` : description,
  };
}
