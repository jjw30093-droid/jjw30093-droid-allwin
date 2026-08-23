import type { MetadataRoute } from "next";
import { PUBLIC_LEAGUES, SITE_URL } from "@/lib/site";

/**
 * sitemap 只列匿名可完整浏览的页面。
 *
 * 2026-08-16 起除"每日精选"外全站比赛内容匿名可完整浏览,PUBLIC_LEAGUES
 * 是已经真实核对过有内容的联赛清单(与后端 LEAGUE_META 对齐,见 lib/site.ts
 * 顶部注释)。逐场比赛详情页数量大且随赛程滚动,MVP 阶段不枚举(避免列出
 * 已过期/空数据页),爬虫可从 /matches 列表页发现详情链接。
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

  const leaguePages: MetadataRoute.Sitemap = PUBLIC_LEAGUES.flatMap(({ id }) => [
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
