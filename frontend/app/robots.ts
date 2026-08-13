import type { MetadataRoute } from "next";
import { LOGIN_ONLY_LEAGUE_IDS, SITE_URL } from "@/lib/site";

/**
 * 爬虫边界与 sitemap.ts 同一真源(lib/site.ts):
 * - 需登录/私有面(账户、后台、Studio、登录页)与 API 全部 Disallow;
 * - 需登录的 top5 联赛页对匿名是付费墙壳,Disallow 防止空壳被索引
 *   (与 sitemap 的收窄口径一致,双向对齐)。
 */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        disallow: [
          "/account",
          "/admin",
          "/studio",
          "/login",
          "/api/",
          ...LOGIN_ONLY_LEAGUE_IDS.map((id) => `/league/${id}/`),
        ],
      },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
  };
}
