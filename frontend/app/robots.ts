import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/site";

/**
 * 爬虫边界与 sitemap.ts 同一真源(lib/site.ts):
 * 需登录/私有面(账户、后台、Studio、登录页)与 API 全部 Disallow。
 * 2026-08-16 起除"每日精选"外全站比赛内容匿名可完整浏览,不存在任何
 * 联赛级登录墙,因此不再对任何 /league/{id}/ 路径做 Disallow。
 */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        disallow: ["/account", "/admin", "/studio", "/login", "/api/"],
      },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
  };
}
