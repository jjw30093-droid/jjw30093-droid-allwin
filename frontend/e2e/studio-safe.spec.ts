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

  // 「禁区威胁」卡必须真的画出一张射门分布图,而不是只有 CSS 双色条。
  // 背景:安全版此前一张图表都没有(runtime/studio/ 里 24 张导出 PNG 中
  // 下方约 25% 是纯空白),而 Studio 是站长的引流素材生产线。
  // 图存在的前提是双方球队有历史射门数据(E2E 用的是真实 allwin.db 副本);
  // 没有时应当优雅退化成"只有指标条",绝不能报错或留破框。
  // 断言是无条件的:E2E 种子固定用 5868011(阿拉维斯 vs 赫塔菲),已核实其
  // analysis bundle 含 recent_shot_map 且带 209 个真实射门点。图不出来就是回归。
  const threatCard = page.getByTestId("safe-scene-threat").first();
  await expect(threatCard.getByText("近 5 场射门落点").first()).toBeVisible();
  // 图表容器里必须有真实渲染出来的 canvas(ECharts 挂载成功,不是空 div)
  await expect
    .poll(async () => threatCard.locator("canvas").count(), { timeout: 20_000 })
    .toBeGreaterThan(0);
  // 导出模式不得渲染任何交互按钮 —— 截进 PNG 的按钮是死的,只会占版面并误导读者
  await expect(
    threatCard.getByRole("button", { name: /本队射门|对手射门|同联赛|全部比赛/ }),
  ).toHaveCount(0);
});
