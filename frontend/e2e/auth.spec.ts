import { test, expect } from "@playwright/test";
import { API, approveViaWebhook, seedMatchId } from "./helpers";

/**
 * 扫码登录(webhook 批准)→ 会员解锁 → Admin 拒绝。
 * 三段可见性:登录即 member 基线,无需订阅(CLAUDE.md §8)。
 */

test("扫码登录 → 会员完整概率投影(API)→ 非管理员被拒", async ({ page }) => {
  // 登录页自动创建扫码请求;模拟微信服务器 webhook 批准后页面轮询领取并跳转
  const deviceRespPromise = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/auth/wechat/device") &&
      r.request().method() === "POST",
  );
  await page.goto("/login?next=/account");
  const device = (await (await deviceRespPromise).json()) as {
    request_id: string;
  };
  const scan = await approveViaWebhook(page.request, device.request_id);
  expect(scan.status()).toBe(200);
  await page.waitForURL("**/account", { timeout: 20_000 });

  // 账户页显示会员身份与权限状态卡(登录价值一眼可见)
  await expect(page.getByText("E2E会员").first()).toBeVisible();
  await expect(page.getByText("当前身份").first()).toBeVisible();
  await expect(page.getByText("注册会员").first()).toBeVisible();
  await expect(page.getByText("完整足球数据").first()).toBeVisible();
  await expect(page.getByText("历史推荐战绩").first()).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByTestId("mobile-bottom-nav").getByRole("link", { name: "我的" }),
  ).toHaveAttribute("href", "/account");

  // 会员解锁(CLAUDE.md §8.2 红线,API 层):登录后三项概率字段齐全。
  // 详情页 UI 不再渲染任何概率(模型未经真实训练前不可用,见 §四②),
  // 权限投影本身仍必须正确——断言从页面文字移到响应体。
  const id = seedMatchId();
  const predRes = await page.request.get(`${API}/api/v1/matches/${id}/prediction`);
  expect(predRes.ok()).toBeTruthy();
  const pred = (await predRes.json()).prediction as {
    tier: string;
    home_probability: number;
    draw_probability: number;
    away_probability: number;
  };
  expect(pred.tier).toBe("full");
  expect(pred.home_probability).toBeCloseTo(0.48);
  expect(pred.draw_probability).toBeCloseTo(0.27);
  expect(pred.away_probability).toBeCloseTo(0.25);

  await page.goto(`/matches/${id}`);
  await expect(page.getByText("48%")).toHaveCount(0);
  await expect(page.getByText("27%")).toHaveCount(0);
  await expect(page.getByText("25%")).toHaveCount(0);

  // 每日精选:普通登录默认打开「历史战绩」;「今日精选」为锁定说明,
  // 不先看到解锁引导再往下找自己已经可以看的战绩
  await page.goto("/reco");
  await expect(page.getByText("战绩归档").first()).toBeVisible();
  await page.getByRole("link", { name: "今日精选" }).click();
  await expect(page.getByText("尚未获得查看权限").first()).toBeVisible();

  // 非管理员访问 /admin:服务端 403,页面整页"无权限"
  await page.goto("/admin");
  await expect(page.getByText("无权限").first()).toBeVisible();
});
