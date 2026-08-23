/**
 * 验收返工五:比赛详情页不得展示"近期比赛状态"——球队数据顶部两张
 * "近期表现" FormList 卡片、QuickView 里的近 N 场胜平负/场均入球摘要,
 * 用户已经明确表示不要这个模块(同类网站已经大量提供,不是本站差异化)。
 *
 * 2026-08-23 单栏重排同时改了 QuickView 的可见性规则(见
 * MatchDetailBody.tsx::QuickView):没有已发布精选时,"推荐待发布"这种
 * 内部发布流程状态不再展示给用户,整个提示框直接不渲染;种子比赛未发布
 * 精选,因此这里断言 quick-view 不出现,而不是断言它出现且写着"推荐待
 * 发布"(旧断言随行为变化一并更新,不是放松验收标准)。
 *
 * 另外原来的"数据"外层 tab 已随单栏重排废除(见 MatchPreTabs.tsx),
 * 内容常驻纵向铺开,不需要先点开外层 tab 才能看到"球队数据"标题
 * (原"数据可视化",同一次重排改名)。
 */

import { test, expect } from "@playwright/test";
import { seedMatchId } from "./helpers";

test("QuickView 无已发布精选时不渲染(不暴露内部发布流程状态),且不展示近 N 场胜平负/场均入球", async ({ page }) => {
  const id = seedMatchId();
  await page.goto(`/matches/${id}`);

  // 种子未发布推荐单 → 整个提示框不渲染,不是展示"推荐待发布"。
  await expect(page.getByTestId("quick-view")).toHaveCount(0);
  await expect(page.getByText("推荐待发布")).toHaveCount(0);
  // 近 N 场胜平负摘要不得出现。
  await expect(page.getByText(/胜.*平.*负/)).toHaveCount(0);
  await expect(page.getByText(/场均入球/)).toHaveCount(0);
});

test("球队数据不再展示两张\"近期表现\" FormList 卡片", async ({ page }) => {
  const id = seedMatchId();
  await page.goto(`/matches/${id}`);

  // 单栏纵向布局:内容常驻 DOM,不需要先点开外层 tab。
  await expect(page.getByText(/近期表现/)).toHaveCount(0);
  // "球队数据"标题本身还在(下面接的是阵容/风格/球员/射门 pill 选项卡),
  // 只是不再直接铺两张近期战绩卡片。
  await expect(page.getByRole("heading", { name: "球队数据" })).toBeVisible();
});
