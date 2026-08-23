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
  const leagueLines = PUBLIC_LEAGUES.map(
    ({ id, nameZh }) =>
      `- [${nameZh}积分榜](${SITE_URL}/league/${id}/standings):${nameZh}最新积分榜与球队数据\n` +
      `- [${nameZh}赛程与比赛](${SITE_URL}/league/${id}/matches):赛程、比分与比赛数据入口`,
  ).join("\n");

  return `# 欧赢 ALLWIN

> 面向中文用户的足球数据与比赛分析站点:联赛积分榜、赛程比分、球队与球员数据、
> 模型完整概率、完整赔率时间线与公开可核验的预测战绩——全部内容匿名即可
> 完整浏览,无需登录;仅"每日精选"独家推荐板块需要站长按场为账号单独授权。

## 核心页面

- [首页](${SITE_URL}/):今日焦点比赛与最新数据
- [比赛列表](${SITE_URL}/matches):按日期浏览比赛、比分与模型概率
- [联赛导航](${SITE_URL}/leagues):全部可浏览联赛入口
- [公开战绩](${SITE_URL}/track-record):模型预测的公开记录与赛后评估(不可选择性删除)
- [模型说明](${SITE_URL}/about-model):模型方法、评估口径与已知局限

## 联赛数据(匿名可完整浏览)

${leagueLines}

## 说明

- 概率与分析均为统计模型输出,存在不确定性,不构成任何形式的收益承诺。
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
