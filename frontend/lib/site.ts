/**
 * 站点级常量:对外规范域名 + 匿名可完整浏览的联赛清单。
 *
 * NEXT_PUBLIC_SITE_URL 必须在 next build 前确定(CLAUDE.md §10.3:NEXT_PUBLIC_*
 * 全部构建期内联);未设置时回退到占位域名——sitemap/robots/llms.txt 消费本值,
 * 部署脚本应在生产构建前注入真实域名。
 *
 * ANON_LEAGUES / LOGIN_ONLY_LEAGUE_IDS 必须与后端单一真源
 * backend/queries/leagues.py 的 LEAGUE_META + FREE_LEAGUE_ENTITLEMENTS 一致
 * (2026-08-11 起,platform 0012 迁移):
 * - league:epl / league:top5(五大联赛)/ league:european_cup(欧冠系)→ 匿名免费;
 * - league:lottery(中国竞彩常见非五大联赛,如瑞典超/挪超/日职/韩K/澳超等)→ 需登录。
 * 这两份列表曾经反过来写(把 lottery 当匿名、把 top5 当需登录),已于 2026-08-15
 * 核对后端并修正——错误期间 sitemap/robots/llms.txt 一直在把免费的五大联赛挡在
 * 收录外、把需登录的联赛推给爬虫。新增/调整联赛权益时,只改
 * backend/queries/leagues.py,再手动同步这里(TS 侧不能直接 import 后端字典)。
 */

export const SITE_URL = (
  process.env.NEXT_PUBLIC_SITE_URL ?? "https://allwin.example.com"
).replace(/\/$/, "");

/** 匿名可完整浏览的联赛(id → 中文名),与后端 free 档 entitlement 对齐。 */
export const ANON_LEAGUES: ReadonlyArray<{ id: number; nameZh: string }> = [
  { id: 47, nameZh: "英超" },
  { id: 87, nameZh: "西甲" },
  { id: 55, nameZh: "意甲" },
  { id: 54, nameZh: "德甲" },
  { id: 53, nameZh: "法甲" },
  { id: 42, nameZh: "欧冠" },
  { id: 73, nameZh: "欧联" },
  { id: 10216, nameZh: "欧协联" },
];

/** 需登录的联赛(不进 sitemap/llms.txt)。 */
export const LOGIN_ONLY_LEAGUE_IDS: ReadonlyArray<number> = [
  67, 59, 223, 9080, 113, 48, 57, 61, 268,
];
