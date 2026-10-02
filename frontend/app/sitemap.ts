import type { MetadataRoute } from "next";
import { isAnonLeague } from "@/lib/login-gate";
import { PUBLIC_LEAGUES, SITE_URL } from "@/lib/site";

/**
 * sitemap 只列匿名可完整浏览的页面。
 *
 * 2026-10-02 登录门禁:未登录只能看英超,其他联赛的页面对爬虫只是登录跳转
 * 壳,不进 sitemap(PUBLIC_LEAGUES 按 isAnonLeague 过滤)。逐场比赛详情页数量大且随赛程滚动,MVP 阶段不枚举(避免列出
 * 已过期/空数据页),爬虫可从 /matches 列表页发现详情链接。
 *
 * 需登录页面(/account、/studio、/admin)与登录页本身不进 sitemap。
 */
export default function sitemap(): MetadataRoute.Sitemap {
  const staticPages: MetadataRoute.Sitemap = [
    { url: `${SITE_URL}/`, changeFrequency: "daily", priority: 1 },
    { url: `${SITE_URL}/matches`, changeFrequency: "daily", priority: 0.9 },
    // 历史战绩(2026-09-16 起有独立路由):匿名可完整浏览的运营记录,
    // 每有一单结算就变,按 daily。
    { url: `${SITE_URL}/track-record`, changeFrequency: "daily", priority: 0.6 },
    { url: `${SITE_URL}/leagues`, changeFrequency: "weekly", priority: 0.7 },
    { url: `${SITE_URL}/pricing`, changeFrequency: "monthly", priority: 0.5 },
    { url: `${SITE_URL}/about`, changeFrequency: "monthly", priority: 0.3 },
  ];

  const leaguePages: MetadataRoute.Sitemap = PUBLIC_LEAGUES.filter(({ id }) => isAnonLeague(id)).flatMap(({ id }) => [
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
