/**
 * 站点级常量:对外规范域名 + 匿名可完整浏览的联赛清单。
 *
 * NEXT_PUBLIC_SITE_URL 必须在 next build 前确定(CLAUDE.md §10.3:NEXT_PUBLIC_*
 * 全部构建期内联);未设置时回退到 http://localhost:3000(localhost 自证不可
 * 索引,不会污染真实爬虫,同时不阻断本机构建与 E2E 构建),部署脚本应在生产
 * 构建前注入真实域名。
 *
 * PUBLIC_LEAGUES 必须与后端单一真源 backend/queries/leagues.py 的 LEAGUE_META
 * 一致(2026-08-16 起,除"每日精选"外全站比赛内容对匿名与登录用户完全一致,
 * 不存在任何联赛级登录墙)。这里只列已经真实核对过有内容的联赛;欧冠/欧联/
 * 欧协联(id 42/73/10216)在 LEAGUE_META 里存在但 dim_match 目前 0 行数据,
 * 暂不列入公开清单。新增/调整联赛时,只改 backend/queries/leagues.py,再
 * 手动同步这里(TS 侧不能直接 import 后端字典)。
 */

export const SITE_URL = (
  process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000"
).replace(/\/$/, "");

/** 匿名可完整浏览的联赛(id → 中文名),与后端 LEAGUE_META 对齐。 */
export const PUBLIC_LEAGUES: ReadonlyArray<{ id: number; nameZh: string }> = [
  { id: 47, nameZh: "英超" },
  { id: 87, nameZh: "西甲" },
  { id: 55, nameZh: "意甲" },
  { id: 54, nameZh: "德甲" },
  { id: 53, nameZh: "法甲" },
  { id: 67, nameZh: "瑞典超" },
  { id: 59, nameZh: "挪威超" },
  { id: 223, nameZh: "日职联" },
  { id: 9080, nameZh: "韩K联" },
  { id: 113, nameZh: "澳超" },
  { id: 48, nameZh: "英冠" },
  { id: 57, nameZh: "荷甲" },
  { id: 61, nameZh: "葡超" },
  { id: 268, nameZh: "巴甲" },
];
