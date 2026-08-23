import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * 站点常量纪律的仓库级守卫。
 *
 * 1) frontend/lib/site.ts 的 SITE_URL 回退值曾经是占位域名
 *    "https://allwin.example.com"——对应的 NEXT_PUBLIC_SITE_URL 全仓从未
 *    配置,导致生产 sitemap.xml/robots.txt/llms.txt 全部指向了这个不存在
 *    的占位域名。回退值必须是自证不可索引的本地地址(如
 *    http://localhost:3000),不能再是任何形式的 example.* 占位域名。
 * 2) PUBLIC_LEAGUES(sitemap/robots/llms 消费的"匿名可完整浏览的联赛"清单)
 *    必须是 components/matches/zh.ts 的 LEAGUE_ZH(比赛卡片展示用的联赛
 *    中文名字典)的子集,且中文名一致——lib/site.ts 顶部注释已经写明
 *    "TS 侧不能直接 import 后端字典,新增/调整联赛时需要手动同步",这条
 *    测试是把那句话变成机器可检查的约束,防止两份清单静默漂移。
 */

const ROOT = join(__dirname, "..");

/** 提取形如 `marker ... = { ... };` 的对象/数组字面量文本(不含外层大括号)。 */
function extractLiteralBody(src: string, marker: string, openChar: "{" | "["): string {
  const markerIdx = src.indexOf(marker);
  expect(markerIdx, `未能在源文件里找到 "${marker}"`).toBeGreaterThanOrEqual(0);
  const openIdx = src.indexOf(openChar, markerIdx);
  expect(openIdx, `未能在 "${marker}" 之后找到 "${openChar}"`).toBeGreaterThan(markerIdx);
  const closeChar = openChar === "{" ? "}" : "]";
  const closeIdx = src.indexOf(`${closeChar};`, openIdx);
  expect(closeIdx, `未能在 "${marker}" 之后找到匹配的 "${closeChar};"`).toBeGreaterThan(openIdx);
  return src.slice(openIdx + 1, closeIdx);
}

describe("站点常量与联赛清单一致性", () => {
  const siteSrc = readFileSync(join(ROOT, "lib/site.ts"), "utf-8");
  const zhSrc = readFileSync(join(ROOT, "components/matches/zh.ts"), "utf-8");

  it("SITE_URL 回退值不是占位域名", () => {
    const match = siteSrc.match(/NEXT_PUBLIC_SITE_URL\s*\?\?\s*"([^"]+)"/);
    expect(match, "未能在 lib/site.ts 里找到 SITE_URL 的回退值定义").not.toBeNull();
    const fallback = match![1];
    expect(
      fallback.includes("example."),
      `SITE_URL 回退值 "${fallback}" 含占位域名片段 "example."——生产忘记设置 ` +
        "NEXT_PUBLIC_SITE_URL 时,sitemap.xml/robots.txt/llms.txt 会全部指向它",
    ).toBe(false);
  });

  it("PUBLIC_LEAGUES 是 LEAGUE_ZH 的子集,且中文名一致", () => {
    const leagueZhBody = extractLiteralBody(zhSrc, "LEAGUE_ZH", "{");
    const leagueZh: Record<number, string> = {};
    for (const m of leagueZhBody.matchAll(/(\d+):\s*"([^"]+)"/g)) {
      leagueZh[Number(m[1])] = m[2];
    }
    expect(
      Object.keys(leagueZh).length,
      "未能从 components/matches/zh.ts 的 LEAGUE_ZH 解析出任何联赛条目",
    ).toBeGreaterThan(0);

    const publicLeaguesBody = extractLiteralBody(siteSrc, "PUBLIC_LEAGUES", "[");
    const publicLeagues: Array<{ id: number; nameZh: string }> = [];
    for (const m of publicLeaguesBody.matchAll(
      /\{\s*id:\s*(\d+),\s*nameZh:\s*"([^"]+)"\s*\}/g,
    )) {
      publicLeagues.push({ id: Number(m[1]), nameZh: m[2] });
    }
    expect(
      publicLeagues.length,
      "未能从 lib/site.ts 的 PUBLIC_LEAGUES 解析出任何联赛条目",
    ).toBeGreaterThan(0);

    for (const { id, nameZh } of publicLeagues) {
      expect(
        leagueZh[id],
        `PUBLIC_LEAGUES 里的联赛 id=${id}(${nameZh})在 LEAGUE_ZH 里不存在或中文名不一致`,
      ).toBe(nameZh);
    }
  });
});
