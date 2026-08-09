import { expect, test } from "@playwright/test";

const MATCH_ID = 5104968;
const CORE_PAGES = [
  "/",
  "/leagues",
  "/matches",
  "/matches?status=upcoming",
  `/matches/${MATCH_ID}`,
  "/league/59/standings",
  "/pricing",
  "/about-model",
  "/track-record",
];
const VIEWPORTS = [
  { width: 360, height: 800 },
  { width: 390, height: 844 },
  { width: 430, height: 932 },
  { width: 1280, height: 800 },
];

test("freshness、联赛、比赛排序和球队显示均为真实公开投影", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByText("数据等待刷新").first()).toBeVisible();
  await expect(page.getByText("LIVE", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "联赛数据" })).toHaveAttribute(
    "href",
    "/leagues",
  );

  await page.goto("/leagues");
  const eliteserien = page.getByRole("heading", { name: "挪威超", exact: true });
  await expect(eliteserien).toBeVisible();
  await expect(
    eliteserien.locator("xpath=ancestor::article").getByText("已有真实数据"),
  ).toBeVisible();

  await page.goto("/matches");
  await expect(page.getByText("未来七天", { exact: true })).toBeVisible();
  await expect(page.getByText("已有分析", { exact: true })).toBeVisible();
  await expect(page.getByText("已有赔率", { exact: true })).toBeVisible();
  await expect(page.getByPlaceholder("中文名或英文名")).toBeVisible();
  const matchLinks = page.locator('a[href^="/matches/"]');
  await expect(matchLinks.nth(0)).toContainText("瓦勒伦加");
  await expect(matchLinks.nth(1)).toContainText("Bodø/Glimt");

  await page.getByRole("link", { name: "浏览联赛排名与球队数据" }).click();
  await expect(page).toHaveURL(/\/leagues$/);
  const eliteserienCard = page
    .getByRole("heading", { name: "挪威超", exact: true })
    .locator("xpath=ancestor::article");
  await eliteserienCard.getByRole("link", { name: "排名", exact: true }).click();
  await expect(page).toHaveURL(/\/league\/59\/standings$/);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/league/59/standings");
  await expect(page.locator("tbody tr")).toHaveCount(16);
  await expect(page.getByText(/Team\s+\d+/)).toHaveCount(0);
  await expect(page.getByText(/#[0-9A-F]{6}/i)).toHaveCount(0);
  await expect(page.getByText("手机可左右滑动查看完整数据")).toBeVisible();
});

test("匿名概率和 analysis 受限字段物理缺失", async ({ request }) => {
  const prediction = await request.get(`/api/v1/matches/${MATCH_ID}/prediction`);
  expect(prediction.ok()).toBeTruthy();
  const predictionText = await prediction.text();
  expect(predictionText).toContain('"tier":"free"');
  expect(predictionText).not.toContain("home_probability");
  expect(predictionText).not.toContain("draw_probability");
  expect(predictionText).not.toContain("away_probability");

  const analysis = await request.get(`/api/v1/matches/${MATCH_ID}/analysis`);
  expect(analysis.ok()).toBeTruthy();
  const analysisBody = await analysis.json();
  expect(analysisBody.prediction_member).toBeNull();
  expect(analysisBody.odds_timeline).toEqual([]);
});

test("四视口、深浅主题、核心页面资产与控制台无回归", async ({ page }) => {
  const consoleErrors: string[] = [];
  const missingAssets: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("response", (response) => {
    if (
      response.status() === 404 &&
      response.url().includes("/_next/static/")
    ) {
      missingAssets.push(response.url());
    }
  });

  for (const viewport of VIEWPORTS) {
    await page.setViewportSize(viewport);
    for (const path of CORE_PAGES) {
      await page.goto(path);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
      );
      expect(overflow, `${viewport.width}x${viewport.height} ${path}`).toBeLessThanOrEqual(
        0,
      );
    }
  }

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const themeButton = page.getByRole("button", { name: /切换为(浅色|深色)模式/ });
  await themeButton.click();
  await page.goto("/pricing");
  await expect(page.locator("html")).toHaveAttribute("data-theme", /light|dark/);

  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
  expect(missingAssets, missingAssets.join("\n")).toEqual([]);
});
