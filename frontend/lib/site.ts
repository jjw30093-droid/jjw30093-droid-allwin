/**
 * 站点级常量:对外规范域名 + 匿名可完整浏览的联赛清单。
 *
 * NEXT_PUBLIC_SITE_URL 必须在 next build 前确定(CLAUDE.md §10.3:NEXT_PUBLIC_*
 * 全部构建期内联);未设置时回退到占位域名——sitemap/robots/llms.txt 消费本值,
 * 部署脚本应在生产构建前注入真实域名。
 *
 * ANON_LEAGUE_IDS 与后端 backend/queries/leagues.py 的 LEAGUE_META 对齐:
 * league:epl 与 league:lottery 属匿名(free)权益;league:top5(西甲/意甲/德甲/法甲)
 * 需登录——匿名访问是付费墙壳页,绝不能进 sitemap/llms.txt(列了只会让爬虫抓到
 * 空壳,SEO 与 AI 爬虫信任双输)。
 */

export const SITE_URL = (
  process.env.NEXT_PUBLIC_SITE_URL ?? "https://allwin.example.com"
).replace(/\/$/, "");

/** 匿名可完整浏览的联赛(id → 中文名),与后端 free 档 entitlement 对齐。 */
export const ANON_LEAGUES: ReadonlyArray<{ id: number; nameZh: string }> = [
  { id: 47, nameZh: "英超" },
  { id: 67, nameZh: "瑞典超" },
  { id: 59, nameZh: "挪威超" },
  { id: 223, nameZh: "日职联" },
  { id: 9080, nameZh: "韩K联" },
  { id: 113, nameZh: "澳超" },
];

/** 需登录的联赛(不进 sitemap/llms.txt)。 */
export const LOGIN_ONLY_LEAGUE_IDS: ReadonlyArray<number> = [87, 55, 54, 53];
