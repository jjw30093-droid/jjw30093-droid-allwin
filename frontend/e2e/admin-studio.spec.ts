import { test, expect } from "@playwright/test";
import { loginWithPassword, seedMatchId } from "./helpers";

/**
 * 管理员密码登录 → Admin 后台可用 → Studio 建草稿 + 导出(TXT 下载事件)。
 */

test("管理员登录 → admin 可用 → studio 导出", async ({ page }) => {
  test.setTimeout(120_000);

  await loginWithPassword(page, "e2e-admin", "e2e-password-123");
  await page.waitForURL("**/");

  // Admin 后台:非"无权限",能看到用户管理
  await page.goto("/admin");
  await expect(page.getByText("无权限")).toHaveCount(0);
  await expect(page.getByText(/用户/).first()).toBeVisible();

  // Studio:门禁通过,创建草稿进入编辑器
  await page.goto("/studio");
  await expect(page.getByText("Creator Studio").first()).toBeVisible();
  const id = seedMatchId();
  await page.getByRole("button", { name: /创建草稿|打开草稿/ }).first().click();
  await page.waitForURL("**/studio/matches/**");

  // 编辑器载入(导出区可见)后触发 TXT 导出,等真实下载事件
  await expect(page.getByText("导出").first()).toBeVisible();
  const dlPromise = page.waitForEvent("download", { timeout: 60_000 });
  await page.getByRole("button", { name: /TXT 口播稿/ }).click();
  const dl = await dlPromise;
  expect(dl.suggestedFilename().toLowerCase()).toContain("txt");
  void id;
});
