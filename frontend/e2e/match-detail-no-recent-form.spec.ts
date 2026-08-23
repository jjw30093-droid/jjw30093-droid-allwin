/**
 * 验收返工五:比赛详情页不得展示"近期比赛状态"——数据可视化顶部两张
 * "近期表现" FormList 卡片、QuickView 里的近 N 场胜平负/场均入球摘要,
 * 用户已经明确表示不要这个模块(同类网站已经大量提供,不是本站差异化)。
 * 推荐发布状态(reco 提示)必须保留,不能用另一套近期战绩换皮替代。
 */

import { test, expect } from "@playwright/test";
import { seedMatchId } from "./helpers";

test("QuickView 不展示近 N 场胜平负/场均入球,但保留推荐发布状态提示", async ({ page }) => {
  const id = seedMatchId();
  await page.goto(`/matches/${id}`);

  const quick = page.getByTestId("quick-view");
  await expect(quick).toBeVisible();
  // reco 提示保留(种子未发布推荐单 → 如实"推荐待发布")。
  await expect(quick.getByText("推荐待发布")).toBeVisible();
  // 近 N 场胜平负摘要不得出现。
  await expect(quick.getByText(/胜.*平.*负/)).toHaveCount(0);
  await expect(quick.getByText(/场均入球/)).toHaveCount(0);
});

test("数据可视化不再展示两张\"近期表现\" FormList 卡片", async ({ page }) => {
  const id = seedMatchId();
  await page.goto(`/matches/${id}`);
  await page.getByRole("tab", { name: "数据" }).click();

  await expect(page.getByText(/近期表现/)).toHaveCount(0);
  // "数据可视化"标题本身还在(下面接的是阵容/风格/球员子 tab),
  // 只是不再直接铺两张近期战绩卡片。
  await expect(page.getByRole("heading", { name: "数据可视化" })).toBeVisible();
});
