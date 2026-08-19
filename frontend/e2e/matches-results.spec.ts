import { test, expect } from "@playwright/test";

/**
 * 「赛果」入口 + 向过去的时间窗(2026-08-19)。
 *
 * 站长报告:已经结束的比赛在前端没有地方可以看到。审计后确认后端一直能返回
 * 完整赛果(status=finished),缺的是入口与"向过去的时间窗"——window 的
 * 3d/7d 只向未来,除 window=all 外任何组合恒为 0 场;而唯一的状态筛选被收在
 * 默认折叠的「更多筛选」里,站内每一处指向 /matches 的链接又都写死
 * status=upcoming。
 *
 * 这里量真实渲染结果(比分文本、首屏坐标、触控区),不靠经验假设。
 * e2e 种子库(data/e2e/allwin.db)是真实 core 库的完整副本,含 13000+ 场
 * 已完赛比赛及比分,所以赛果列表的内容是可以真实端到端验证的。
 */

test("赛果视图:默认(不带 window)就能看到带比分的已完赛比赛", async ({ page }) => {
  await page.goto("/matches?status=finished");

  await expect(page.getByRole("heading", { name: "赛果", level: 1 })).toBeVisible();

  // 至少一行渲染出"数字 - 数字"的比分。这是本次改动要交付的东西本身,
  // 只断言"页面没报错"是不够的。
  const firstRow = page.locator('a[href^="/matches/"]').first();
  await expect(firstRow).toBeVisible();
  await expect(firstRow).toContainText(/\d+\s*-\s*\d+/);
  await expect(page.getByText("已完赛").first()).toBeVisible();
});

test("赛果视图的时间筛选是向过去的,且点击后不会把用户打回赛程", async ({ page }) => {
  await page.goto("/matches?status=finished");

  // 时间 chip 换成向过去的一组(数量与赛程侧一致,不新增行)
  for (const label of ["今天", "昨天", "近三天", "近七天", "全部赛果"]) {
    await expect(page.getByRole("link", { name: label, exact: true })).toBeVisible();
  }
  await expect(page.getByRole("link", { name: "未来七天", exact: true })).toHaveCount(0);

  // 真实缺陷回归:时间 chip 曾硬编码 status:"upcoming",点一下就被弹回赛程。
  await page.getByRole("link", { name: "近七天", exact: true }).click();
  await expect(page).toHaveURL(/status=finished/);
  await expect(page).toHaveURL(/window=past7d/);
  await expect(page.getByRole("heading", { name: "赛果", level: 1 })).toBeVisible();
});

test("赛程 ↔ 赛果 切换器在两个方向都改写时间窗(不留下恒为 0 场的组合)", async ({
  page,
}) => {
  await page.goto("/matches");
  await expect(page.getByRole("heading", { name: "比赛", level: 1 })).toBeVisible();

  await page.getByRole("link", { name: "赛果", exact: true }).first().click();
  await expect(page).toHaveURL(/status=finished/);
  await expect(page.getByRole("heading", { name: "赛果", level: 1 })).toBeVisible();
  await expect(page.locator('a[href^="/matches/"]').first()).toBeVisible();

  // 切回来:带着 past7d 回赛程会恒空,所以必须同时改写 window
  await page.getByRole("link", { name: "赛程", exact: true }).first().click();
  await expect(page).not.toHaveURL(/past7d|window=all/);
  await expect(page.getByRole("heading", { name: "比赛", level: 1 })).toBeVisible();
});

test("选了一个已经过去的日期却停在赛程视图时,给出到当天赛果的出口而不是白板", async ({
  page,
}) => {
  // 真实缺陷(生产实测):/matches?date=<过去某天> 渲染出一块没有任何解释的
  // 空页,而那天其实有几十场已完赛比赛。
  await page.goto("/matches?date=2026-08-09");

  const exit = page.getByRole("link", { name: /查看 2026-08-09 的赛果/ });
  await expect(exit).toBeVisible();
  const box = await exit.boundingBox();
  expect(box!.height).toBeGreaterThanOrEqual(44);

  await exit.click();
  await expect(page).toHaveURL(/date=2026-08-09.*status=finished|status=finished.*date=2026-08-09/);
  await expect(page.locator('a[href^="/matches/"]').first()).toContainText(/\d+\s*-\s*\d+/);
});

test("移动端:切换器不顶穿首屏预算,触控区达标", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/matches");

  const modes = page.getByRole("group", { name: "赛程或赛果" }).getByRole("link");
  await expect(modes).toHaveCount(2);
  for (const box of await Promise.all(
    (await modes.all()).map((l) => l.boundingBox()),
  )) {
    expect(box!.height).toBeGreaterThanOrEqual(44);
  }

  // 与 e2e/matches-mobile-first-screen.spec.ts 同一条阈值:切换器是加在
  // <h1> 同一行(.header 右侧本来就空着)的,不允许把这条验收顶穿。
  const firstRow = page.locator('a[href^="/matches/"]').first();
  await expect(firstRow).toBeVisible();
  const rowBox = await firstRow.boundingBox();
  expect(rowBox!.y).toBeLessThan(495);

  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test("顶部导航有「赛果」入口,且与「比赛」选中态互斥", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/matches?status=finished");

  const topNav = page.getByRole("navigation", { name: "主导航" });
  await expect(topNav.getByRole("link", { name: "赛果", exact: true })).toBeVisible();
  await expect(
    topNav.locator('a[aria-current="page"]'),
  ).toHaveText(["赛果"]);
});
