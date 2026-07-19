import { test, expect } from "@playwright/test";
import { API, seedMatchId } from "./helpers";

/**
 * 匿名浏览:首页/比赛列表/详情/信任页。
 * 核心断言:免费层只有最高一项概率(种子 48%),另两项(27%/25%)
 * 不出现在页面,也不出现在匿名 API 响应体里(物理省略,非 CSS 遮挡)。
 */

test("首页匿名可浏览", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("开赛前,把一场比赛讲清楚").first()).toBeVisible();
});

test("比赛列表渲染真实数据", async ({ page }) => {
  await page.goto("/matches");
  await expect(page.locator(`a[href*="/matches/"]`).first()).toBeVisible();
});

test("详情页免费概率:只出现最高一项", async ({ page, request }) => {
  const id = seedMatchId();

  // API 层:匿名响应体不含受限字段
  const res = await request.get(`${API}/api/v1/matches/${id}/prediction`);
  expect(res.ok()).toBeTruthy();
  const body = await res.text();
  expect(body).toContain("top_probability");
  expect(body).not.toContain("draw_probability");
  expect(body).not.toContain("away_probability");

  // UI 层:48% 可见,27%/25% 不存在
  await page.goto(`/matches/${id}`);
  await expect(page.getByText("48%").first()).toBeVisible();
  await expect(page.getByText("27%")).toHaveCount(0);
  await expect(page.getByText("25%")).toHaveCount(0);
});

test("公开战绩/模型说明/定价页可访问且诚实", async ({ page }) => {
  await page.goto("/track-record");
  await expect(page.getByText(/正式|口径/).first()).toBeVisible();

  await page.goto("/about-model");
  await expect(page.getByText(/Dixon|校准|RPS/).first()).toBeVisible();

  await page.goto("/pricing");
  await expect(page.getByText("Pro").first()).toBeVisible();
});
