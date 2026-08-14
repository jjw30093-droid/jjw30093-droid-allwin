import type { MetadataRoute } from "next";
import { ANON_LEAGUES, SITE_URL } from "@/lib/site";

/**
 * sitemap 只列匿名可完整浏览的页面(商业模式重构第一部分)。
 *
 * 收窄纪律:league:lottery(瑞典超/挪超/日职/韩K/澳超/英冠/荷甲/葡超/巴甲)
 * 对匿名是登录墙壳页,列进来只会让爬虫抓到空壳——既无 SEO 又降低 AI 爬虫对
 * 整站的信任,因此只列 ANON_LEAGUES(五大联赛 + 欧冠系)。
 * 逐场比赛详情页数量大且随赛程滚动,MVP 阶段不枚举(避免列出已过期/空数据页),
 * 爬虫可从 /matches 列表页发现详情链接。
 *
 * 需登录页面(/account、/studio、/admin)与登录页本身不进 sitemap。
 */
export default function sitemap(): MetadataRoute.Sitemap {
  const staticPages: MetadataRoute.Sitemap = [
    { url: `${SITE_URL}/`, changeFrequency: "daily", priority: 1 },
    { url: `${SITE_URL}/matches`, changeFrequency: "daily", priority: 0.9 },
    { url: `${SITE_URL}/leagues`, changeFrequency: "weekly", priority: 0.7 },
    { url: `${SITE_URL}/track-record`, changeFrequency: "daily", priority: 0.8 },
    { url: `${SITE_URL}/about-model`, changeFrequency: "monthly", priority: 0.5 },
    { url: `${SITE_URL}/pricing`, changeFrequency: "monthly", priority: 0.5 },
    { url: `${SITE_URL}/about`, changeFrequency: "monthly", priority: 0.3 },
  ];

  const leaguePages: MetadataRoute.Sitemap = ANON_LEAGUES.flatMap(({ id }) => [
    {
      url: `${SITE_URL}/league/${id}/standings`,
      changeFrequency: "daily" as const,
      priority: 0.7,
    },
    {
      url: `${SITE_URL}/league/${id}/matches`,
      changeFrequency: "daily" as const,
      priority: 0.7,
    },
    {
      url: `${SITE_URL}/league/${id}/players`,
      changeFrequency: "weekly" as const,
      priority: 0.5,
    },
    {
      url: `${SITE_URL}/league/${id}/team-stats`,
      changeFrequency: "weekly" as const,
      priority: 0.5,
    },
  ]);

  return [...staticPages, ...leaguePages];
}
