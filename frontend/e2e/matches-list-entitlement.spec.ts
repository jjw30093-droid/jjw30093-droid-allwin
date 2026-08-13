import { test, expect } from "@playwright/test";
import { approveViaWebhook } from "./helpers";

/**
 * 回归(2026-08-13 用户报告 + 同日用户拍板产品决策):
 *
 * 第一阶段(已解决,见下方"已解决"段落):/matches 服务端渲染永远是匿名口径
 * (会话 cookie Path=/api/v1,Next RSC 读不到),已登录且持有 league:lottery
 * 的账号打开该页时,联赛筛选栏和比赛行此前和匿名访客一模一样。
 *
 * 第二阶段(同日,产品决策撤销更早的"未持有权限的联赛整场从列表隐藏"):
 * 用户反馈五大联赛季前空窗期匿名列表几乎看不到比赛,拍板改为"比赛本身
 * 对所有人可见,只锁概率内容"——日职联等 league:lottery 联赛现在匿名也能
 * 在列表里看到真实比赛行,筛选栏"登录"角标和 MatchRow 的"登录后查看
 * 胜平负概率"提示才是这次要验证的锁点,不是比赛行本身的有无。
 *
 * MatchListLive 挂载后应带 cookie 重新拉一次 /api/v1/leagues +
 * /api/v1/matches,把匿名渲染换成真实权益对应的内容。已用真实浏览器(非
 * Playwright)手动复现过这条链路本身完全正常;这里在登录跳转落地后加一次
 * page.reload(),避开 Next.js 客户端路由在"claim 成功 → 立即 push"这个
 * 极短时间窗里可能命中的预取缓存(路由缓存服务旧的匿名 RSC payload)——
 * 这是 Playwright 自动化操作间隔远短于真实用户手速时才会暴露的时序问题,
 * 不是本次要修的产品缺陷,加一次真实的整页刷新可以绕开、又不影响验证的
 * 目标(挂载后的浏览器端刷新本身是否生效)。
 */

test("联赛筛选:匿名可见但锁定小联赛概率,登录后按真实权益解锁(不是永远锁着)", async ({
  page,
}) => {
  // 匿名:日职联(league:lottery,与英超等五大联赛不同档)标"登录",
  // 但比赛行本身仍然出现在列表里——只是不下发概率、显示登录提示。
  await page.goto("/matches?league=223&status=upcoming&window=all");
  const lockedChip = page.getByRole("link", { name: "日职联" });
  await expect(lockedChip.filter({ hasText: "登录" })).toBeVisible();
  await expect(page.locator('a[href^="/matches/"]').first()).toBeVisible();
  await expect(page.getByText("登录后查看胜平负概率").first()).toBeVisible();

  // 真实扫码登录(与 auth.spec.ts 同一条 webhook 批准链路)——
  // 登录本身即解锁 member 基线,足球数据不需要额外订阅(CLAUDE.md §8)。
  const deviceRespPromise = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/auth/wechat/device") &&
      r.request().method() === "POST",
  );
  await page.goto("/login?next=%2Fmatches");
  const device = (await (await deviceRespPromise).json()) as {
    request_id: string;
  };
  const scan = await approveViaWebhook(page.request, device.request_id);
  expect(scan.status()).toBe(200);
  await page.waitForURL("**/matches", { timeout: 20_000 });
  await page.reload();

  await page.goto("/matches?league=223&status=upcoming&window=all");

  // 挂载后浏览器带 cookie 刷新一次:日职联从"锁定"变成真实可访问——
  // exact 精确匹配"日职联"(不带登录角标)才算真的解锁,不是角标凑巧还在。
  await expect(page.getByRole("link", { name: "日职联", exact: true })).toBeVisible({
    timeout: 10_000,
  });
  // 登录后锁定提示应当清零——比赛行本来就在,变化的是概率是否解锁。
  await expect(page.getByText("登录后查看胜平负概率")).toHaveCount(0, {
    timeout: 10_000,
  });
  await expect(page.locator('a[href^="/matches/"]').first()).toBeVisible({
    timeout: 10_000,
  });
});
