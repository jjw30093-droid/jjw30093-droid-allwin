import { expect, test } from "@playwright/test";

test("Studio 默认安全版展示六张打法卡且不出现敏感文案", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/login");
  const summary = page.getByText("管理员密码登录");
  await summary.click();
  await page.getByLabel("用户名").fill("e2e-admin");
  await page.getByLabel("密码").fill("e2e-password-123");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForURL("**/");

  await page.goto("/studio");
  await page.getByRole("button", { name: /创建草稿|打开草稿/ }).first().click();
  await page.waitForURL("**/studio/matches/**");

  await expect(page.getByRole("button", { name: "抖音安全版（默认）" })).toBeVisible();
  await expect(page.getByTestId("safe-scene-cover").first()).toBeVisible();
  for (const label of [
    "比赛封面",
    "控球与组织",
    "禁区威胁",
    "边路与定位球",
    "无球与防守",
    "对位总结",
  ]) {
    await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
  }
  const pageText = (await page
    .locator('[data-testid^="safe-scene-"]')
    .allInnerTexts())
    .join("\n");
  for (const term of [
    "胜平负", "主胜", "客胜", "赔率", "盘口", "水位", "投注",
    "推荐", "稳胆", "命中率", "收益", "红单",
  ]) {
    expect(pageText).not.toContain(term);
  }
  await expect(page.locator("body")).not.toHaveCSS("overflow-x", "scroll");
});
