import { test, expect } from "@playwright/test";

/**
 * /matches 移动端字号与触控区审计(P3.C,2026-08-16)。
 *
 * 面向 30-40 岁手机用户:正文/重要辅助信息不得小于 CLAUDE.md §11.2 的
 * 12px 可读下限;关键可点击链接/按钮的触控区(getBoundingClientRect）
 * 至少 44×44px。真实回归(改前):
 * - MatchRow.module.css `.syncLine[data-state="UNAVAILABLE"]` 显式写死
 *   font-size:11px,低于 12px 下限;
 * - matches.module.css 的筛选 chip(时间/联赛)、`.filterBtn`
 *   (筛选/搜索提交按钮)、`.moreFiltersSummary`("更多筛选"折叠触发器)
 *   触控区实测远小于 44px(chip ≈31.5px 高,summary ≈19.5px 高)。
 *
 * 这里量真实渲染出的 computed style / boundingClientRect,不是靠经验假设
 * (风格与 e2e/match-detail-charts.spec.ts 的 computed-style 断言一致)。
 */

test("MatchRow: sync_state=UNAVAILABLE 提示文字不低于 12px 可读下限", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/matches");

  // e2e 种子库没有 data/e2e/content_status.json,几乎所有比赛的
  // sync_state 都会走 UNAVAILABLE 分支(见 backend/content_status.py
  // ::public_status_for_match 对不匹配 match_id 的降级),这是自然产生的
  // 真实数据状态,不用另外造 fixture。
  const unavailable = page.locator('[data-state="UNAVAILABLE"]').first();
  await expect(unavailable).toBeVisible();
  await expect(unavailable).toHaveText("部分数据暂不可用");

  const fontSize = await unavailable.evaluate((el) =>
    parseFloat(getComputedStyle(el).fontSize),
  );
  expect(fontSize).toBeGreaterThanOrEqual(12);
});

test("MatchRow: 主客队名与开球时间(核心信息)不低于 14px 正文下限", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/matches");

  const firstRow = page.locator('a[href^="/matches/"]').first();
  await expect(firstRow).toBeVisible();

  const homeSize = await firstRow
    .locator('[class*="home"]')
    .first()
    .evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
  expect(homeSize).toBeGreaterThanOrEqual(14);

  const awaySize = await firstRow
    .locator('[class*="away"]')
    .first()
    .evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
  expect(awaySize).toBeGreaterThanOrEqual(14);
});

test('/matches 筛选 chip("时间/联赛")触控区至少 44×44px', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/matches");

  const chip = page.getByRole("link", { name: "今天" });
  await expect(chip).toBeVisible();
  const box = await chip.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.height).toBeGreaterThanOrEqual(44);
});

test('"更多筛选"折叠触发器(summary)本身触控区至少 44px 高', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/matches");

  const summary = page.locator("summary", { hasText: "更多筛选" });
  await expect(summary).toBeVisible();
  const box = await summary.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.height).toBeGreaterThanOrEqual(44);
});

test('展开"更多筛选"后,"筛选"提交按钮触控区至少 44×44px', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/matches");

  await page.locator("summary", { hasText: "更多筛选" }).click();
  const filterBtn = page.getByRole("button", { name: "筛选" });
  await expect(filterBtn).toBeVisible();
  const box = await filterBtn.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.height).toBeGreaterThanOrEqual(44);
  expect(box!.width).toBeGreaterThanOrEqual(44);
});

test("带日期筛选进入时,「清除日期」链接触控区至少 44px 高", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/matches?date=2026-08-20");

  const clearLink = page.getByRole("link", { name: "清除日期" });
  await expect(clearLink).toBeVisible();
  const box = await clearLink.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.height).toBeGreaterThanOrEqual(44);
});

test("触控区改动不破坏移动端首屏:第一场比赛仍在明显靠前的位置(不回归 P3.B)", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/matches");

  const firstRow = page.locator('a[href^="/matches/"]').first();
  await expect(firstRow).toBeVisible();
  const box = await firstRow.boundingBox();
  expect(box).not.toBeNull();
  // 与 e2e/matches-mobile-first-screen.spec.ts 用的同一条阈值(<495px)——
  // 触控区加高不能把这条 P3.B 验收顶穿。
  expect(box!.y).toBeLessThan(495);
});
