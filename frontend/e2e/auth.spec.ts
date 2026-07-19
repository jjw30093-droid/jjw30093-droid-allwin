import { test, expect } from "@playwright/test";
import { API, seedMatchId } from "./helpers";

/**
 * Mock 微信登录(development MockWechatProvider)→ 会员解锁 → Admin 拒绝。
 * 种子已给 mock-openid-user-1 绑定 pro 订阅。
 */

test("mock 微信登录 → 会员完整概率 → 非管理员被拒", async ({ page }) => {
  // 顶级导航走 OAuth start,mock provider 302 回 callback,种下会话 cookie 后回前端
  await page.goto(`${API}/api/v1/auth/wechat/oa/start?next=/account`);
  await page.waitForURL("**/account");

  // 账户页显示会员身份与套餐
  await expect(page.getByText("E2E会员").first()).toBeVisible();
  await expect(page.getByText("当前套餐").first()).toBeVisible();
  await expect(page.getByText(/pro/i).first()).toBeVisible();

  // 会员解锁:详情页出现完整三项概率(48/27/25)
  const id = seedMatchId();
  await page.goto(`/matches/${id}`);
  await expect(page.getByText("48%").first()).toBeVisible();
  await expect(page.getByText("27%").first()).toBeVisible();
  await expect(page.getByText("25%").first()).toBeVisible();

  // 非管理员访问 /admin:服务端 403,页面整页"无权限"
  await page.goto("/admin");
  await expect(page.getByText("无权限").first()).toBeVisible();
});
