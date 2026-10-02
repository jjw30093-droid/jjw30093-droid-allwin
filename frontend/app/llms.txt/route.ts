import { isAnonLeague } from "@/lib/login-gate";
import { PUBLIC_LEAGUES, SITE_URL } from "@/lib/site";

/**
 * /llms.txt — 面向 AI 爬虫的站点说明(llmstxt.org 约定)。
 *
 * 与 sitemap.ts / robots.ts 同一真源(lib/site.ts),只描述匿名可完整浏览的
 * 页面:列了需要登录的账户类页面(账户设置、Studio、Admin 等)只会让 AI
 * 爬虫抓到登录墙壳,降低对整站的信任。文案遵守 CLAUDE.md §1:不使用
 * 因果/收益承诺式表述。
 */

export const dynamic = "force-static";

function buildLlmsTxt(): string {
  // 2026-10-02 登录门禁:只列未登录可浏览的联赛(英超)
  const leagueLines = PUBLIC_LEAGUES.filter(({ id }) => isAnonLeague(id)).map(
    ({ id, nameZh }) =>
      `- [${nameZh}积分榜](${SITE_URL}/league/${id}/standings):${nameZh}最新积分榜与球队数据\n` +
      `- [${nameZh}赛程与比赛](${SITE_URL}/league/${id}/matches):赛程、比分与比赛数据入口`,
  ).join("\n");

  return `# 喵弟数据研究室

> 面向中文用户的足球数据与比赛分析站点:联赛积分榜、赛程比分、球队与球员数据、
> 赔率时间线与比赛事件时间轴。英超数据无需登录即可浏览,其他联赛的比赛详情与
> 联赛数据需要登录;"每日精选"板块需要按场为账号单独授权。

## 核心页面

- [首页](${SITE_URL}/):今日焦点比赛与最新数据
- [比赛列表](${SITE_URL}/matches):按日期浏览比赛与比分
- [联赛导航](${SITE_URL}/leagues):全部可浏览联赛入口

## 联赛数据(无需登录)

${leagueLines}

## 说明

- 站内的胜平负概率基于市场数据计算,存在不确定性,不构成任何形式的收益承诺。
- 站内时间默认以北京时间(UTC+8)展示。
`;
}

export function GET(): Response {
  return new Response(buildLlmsTxt(), {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
    },
  });
}
