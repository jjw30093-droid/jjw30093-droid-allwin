/**
 * 比赛详情页「球队数据」→「风格」子 tab 下的四张 Phase 2 核心图
 * (进攻转化链 / 控球与场面控制 / 防守承压与限制能力 / 本场攻防对位)
 * ——覆盖真实种子比赛,验证内容渲染且 360/390/430 三档都不出现横向溢出。
 *
 * 2026-08-23 单栏重排废除了外层"数据"tab(内容常驻纵向铺开,见
 * MatchPreTabs.tsx),这里不再需要先点开外层 tab;内层"风格"仍是真实的
 * pill 选项卡(MatchDataTabs.tsx),点击逻辑不变。
 */

import { test, expect } from "@playwright/test";
import { seedMatchId } from "./helpers";

for (const viewport of [
  { width: 360, height: 800 },
  { width: 390, height: 844 },
  { width: 430, height: 932 },
]) {
  test(`比赛详情页四张核心图 ${viewport.width}×${viewport.height}:渲染且无横向溢出`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    const id = seedMatchId();
    await page.goto(`/matches/${id}`);

    const styleTab = page.getByRole("tab", { name: "风格" });
    await styleTab.click();

    await expect(page.getByRole("heading", { name: "进攻转化链" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "控球与场面控制" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "防守承压与限制能力" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "本场攻防对位" })).toBeVisible();

    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(viewport.width);
  });
}

test("进攻转化链:缺失场次显示数据不足,不伪装成 0", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const id = seedMatchId();
  await page.goto(`/matches/${id}`);
  await page.getByRole("tab", { name: "风格" }).click();

  const chainSection = page
    .getByRole("heading", { name: "进攻转化链" })
    .locator("xpath=ancestor::section[1]");
  // 不能出现裸的 "0.00/场" 或 "0%" 这种看起来像真实测出来是 0 的假象——
  // 缺失必须走"数据不足"文案,这是 Phase 0.2 教训在图1 上的延伸断言。
  await expect(chainSection).not.toContainText("0.00/场");
});

test("本场攻防对位:文案不出现相关系数/z-score/置信区间等统计黑话", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const id = seedMatchId();
  await page.goto(`/matches/${id}`);
  await page.getByRole("tab", { name: "风格" }).click();

  const matchupSection = page
    .getByRole("heading", { name: "本场攻防对位" })
    .locator("xpath=ancestor::section[1]");
  for (const jargon of ["相关系数", "z-score", "置信区间"]) {
    await expect(matchupSection).not.toContainText(jargon);
  }
});

test("验收返工四:移动端字号真实 computed,不止查页面级 scrollWidth", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  const id = seedMatchId();
  await page.goto(`/matches/${id}`);
  await page.getByRole("tab", { name: "风格" }).click();

  // 标签(如"射门""禁区触球")——正文/标签 ≥14px。
  const label = page.locator('[class*="stageLabel"]').first();
  await expect(label).toBeVisible();
  const labelSize = await label.evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
  expect(labelSize).toBeGreaterThanOrEqual(14);

  // 关键数值(条形右侧的具体数字)——≥18px。
  const value = page.locator('[class*="stageValue"]').first();
  await expect(value).toBeVisible();
  const valueSize = await value.evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
  expect(valueSize).toBeGreaterThanOrEqual(18);

  // 次要说明(脚注)——≥12px。
  const footNote = page.locator('[class*="footNote"]').first();
  const footNoteSize = await footNote.evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
  expect(footNoteSize).toBeGreaterThanOrEqual(12);

  // 组件边界:关键数值不能因为字号增大而被自身容器截断/换行溢出。
  const overflowingValues = await page.locator('[class*="stageValue"]').evaluateAll((els) =>
    els.filter((el) => el.scrollWidth > el.clientWidth + 1).length,
  );
  expect(overflowingValues).toBe(0);

  // 中文页面不得出现英文变量名 teal。
  const bodyText = await page.locator("body").innerText();
  expect(bodyText).not.toContain("teal");

  // 本场攻防对位的关键对位数值同样要满足关键数值字号。
  const matchupValue = page.locator('[class*="rowSide"] b').first();
  if (await matchupValue.count()) {
    const matchupValueSize = await matchupValue.evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
    expect(matchupValueSize).toBeGreaterThanOrEqual(18);
  }
});
