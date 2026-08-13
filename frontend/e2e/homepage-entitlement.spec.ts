import { test, expect } from "@playwright/test";
import { approveViaWebhook } from "./helpers";

/**
 * 回归(2026-08-13,与 matches-list-entitlement.spec.ts 同一根因,第一阶段):
 * 首页的今晚/明天/未来7天计数条 + "今日重点"选场 + 近期比赛列表,服务端
 * 渲染同样永远是匿名口径(会话 cookie Path=/api/v1,Next RSC 读不到)。
 *
 * 第二阶段(同日,产品决策撤销"未持有权限的联赛整场从列表隐藏",见
 * matches-list-entitlement.spec.ts 同一处注释):/api/v1/matches 现在对
 * 匿名和登录用户返回同一批比赛(计数条数字登录前后相同,不再是"变多"),
 * 门禁移到了单场的 win_probability 字段——锁定联赛的比赛卡片会显示"登录后
 * 查看…概率"提示,登录后该提示应当消失。
 *
 * 不在测试里重算首页"今日重点/近期比赛"的数据富集度选场算法(见
 * lib/homepage.ts::selectHomepageMatches)来预测锁定提示的精确数量——那会
 * 让这条用例和选场算法的实现细节耦合,算法一调整就得跟着改。改用一个不依赖
 * 具体数字的不变量:匿名态下(真实赛程,未来 7 天大概率横跨多个
 * league:lottery 联赛)至少能看到一张锁定提示卡片,登录后必须清零。
 */

test("首页:锁定联赛比赛卡片的登录提示,登录后按真实权益消失", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("match-counts-bar")).toBeVisible();

  const lockHints = page.getByText(/登录后查看.*概率/);
  // 先确认这条用例真的测到了东西——如果当前真实赛程未来 7 天恰好一场
  // 锁定联赛比赛都没有,后面"登录后清零"的断言会变得毫无意义地必然成立。
  await expect(lockHints.first()).toBeVisible({ timeout: 10_000 });

  const deviceRespPromise = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/auth/wechat/device") &&
      r.request().method() === "POST",
  );
  await page.goto("/login?next=%2F");
  const device = (await (await deviceRespPromise).json()) as {
    request_id: string;
  };
  const scan = await approveViaWebhook(page.request, device.request_id);
  expect(scan.status()).toBe(200);
  await page.waitForURL("**/", { timeout: 20_000 });
  await page.reload();

  // 挂载后浏览器带 cookie 刷新一次:member 基线覆盖全部联赛,锁定提示
  // 应当清零——比赛本来就都在列表里,变化的是概率是否解锁,不是数量。
  await expect(page.getByText(/登录后查看.*概率/)).toHaveCount(0, {
    timeout: 10_000,
  });
});
