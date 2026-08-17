import { test, expect } from "@playwright/test";

/**
 * /matches 移动端首屏信息架构(P3.B,2026-08-16)。
 *
 * 真实回归:390px 视口下所有筛选控件默认全部展开摊平渲染,第一场比赛要滑到
 * y≈495px 才出现。这里量真实渲染结果,而不是靠经验假设。
 */

test("390px 视口下第一场比赛不需要划过大段筛选控件就能看到,更多筛选默认折叠", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/matches");

  // 更多筛选默认折叠:赛季/日期/球队搜索不占首屏空间。
  const moreFilters = page.locator("details", { hasText: "更多筛选" });
  await expect(moreFilters).toBeVisible();
  expect(await moreFilters.evaluate((el) => (el as HTMLDetailsElement).open)).toBe(
    false,
  );
  await expect(page.getByLabel("日期")).toBeHidden();
  await expect(page.getByLabel("球队")).toBeHidden();

  // 主筛选行(时间/内容/联赛)仍然可见。
  await expect(page.getByText("时间", { exact: true })).toBeVisible();
  await expect(page.getByText("联赛", { exact: true })).toBeVisible();

  // 第一场比赛行的垂直位置必须明显靠前(改前 y≈495px)。
  const firstRow = page.locator('a[href^="/matches/"]').first();
  await expect(firstRow).toBeVisible();
  const box = await firstRow.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.y).toBeLessThan(495);

  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
});

test("展开更多筛选后,状态/赛季/日期/球队搜索都可见且可用(无 JS 环境下依然可用的纯 GET 表单)", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/matches");

  const moreFilters = page.locator("details", { hasText: "更多筛选" });
  await moreFilters.locator("summary").click();
  expect(await moreFilters.evaluate((el) => (el as HTMLDetailsElement).open)).toBe(
    true,
  );
  await expect(page.getByLabel("日期")).toBeVisible();
  await expect(page.getByLabel("球队")).toBeVisible();
  await expect(moreFilters.getByText("状态", { exact: true })).toBeVisible();
});

test("已完赛(状态筛选,收在更多筛选里)链接自带 URL 时,更多筛选默认展开,不藏起已选筛选", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/matches?status=finished&window=all");
  const moreFilters = page.locator("details", { hasText: "更多筛选" });
  expect(await moreFilters.evaluate((el) => (el as HTMLDetailsElement).open)).toBe(
    true,
  );
});
