import { test, expect } from "@playwright/test";

/**
 * admin 「每日精选」RecoTab 移动端可用性(P4.B,2026-08-16)。
 *
 * 360/390/430px 三个真实视口下验证:筛选栏、新建表单(真实比赛搜索)、
 * 已创建推荐单卡片、编辑面板、会员预览面板都不需要横向滚动就能完成操作;
 * 发布按钮点击后必须先经过 window.confirm 二次确认(真实浏览器原生对话框,
 * 不是 mock)。
 *
 * E2E 环境的 odds.db 每次重建为空表(tests/e2e/seed_e2e.py 顶部说明),
 * 没有真实 NowGoal 快照可选,因此这里创建的草稿会退回手动录入
 * (entry_type=legacy_manual)——真实盘口选项下拉的港盘/十进制/新鲜度中文
 * 展示由 tests/admin-reco-tab.test.tsx(Vitest,可完全控制假数据)覆盖,
 * 这里验证的是"选不到真实盘口时,发布会被后端拒绝,拒绝原因在窄屏下完整
 * 可读"这条同样真实存在的路径。
 */

const VIEWPORTS = [
  { width: 360, height: 800, label: "360px" },
  { width: 390, height: 844, label: "390px" },
  { width: 430, height: 932, label: "430px" },
];

async function loginAsAdmin(page: import("@playwright/test").Page) {
  await page.goto("/login");
  const summary = page.getByText("管理员密码登录");
  await summary.click();
  try {
    await expect(page.getByLabel("用户名")).toBeVisible({ timeout: 3000 });
  } catch {
    await summary.click();
  }
  await page.getByLabel("用户名").fill("e2e-admin");
  await page.getByLabel("密码").fill("e2e-password-123");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForURL("**/");
}

/** document.documentElement.scrollWidth 不应超过视口宽度——超过即代表存在
 * 横向滚动,窄屏用户需要拖动才能看全内容(任务书 §8 明确禁止)。允许 1px
 * 容差(子像素取整)。 */
async function expectNoHorizontalScroll(page: import("@playwright/test").Page, width: number) {
  const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(scrollWidth).toBeLessThanOrEqual(width + 1);
}

for (const vp of VIEWPORTS) {
  test(`管理员每日精选 @ ${vp.label}:筛选/新建/编辑/预览/发布确认全程无需横向滚动`, async ({ page }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width: vp.width, height: vp.height });

    await loginAsAdmin(page);
    await page.goto("/admin");
    await expect(page.getByText("无权限")).toHaveCount(0);
    await page.getByRole("button", { name: "每日精选" }).click();

    // 筛选栏
    await expect(page.getByLabel("状态筛选")).toBeVisible();
    await expect(page.getByLabel("起始日期")).toBeVisible();
    await expectNoHorizontalScroll(page, vp.width);

    // 新建表单:真实比赛搜索(核心库是真实数据拷贝,不 mock)
    const matchInput = page.getByPlaceholder("比赛(搜索队名,或手动填写描述)");
    await matchInput.click();
    await page.waitForTimeout(400); // 300ms 防抖
    await expectNoHorizontalScroll(page, vp.width);

    const firstCandidate = page.locator('button[class*="matchDropdownItem"]').first();
    const hasCandidate = (await firstCandidate.count()) > 0;
    if (hasCandidate) {
      await firstCandidate.click();
      // 选中真实比赛后,该场没有真实盘口(E2E odds.db 空表)时会保留手动
      // 赔率三格(showManualOddsFields = !hasOptions),仍需要补全选项/赔率
      // 才是一条合法的腿——这与"退回手动描述"分支需要的字段完全一致。
    } else {
      // 候选窗口内恰好没有未开赛比赛时(理论上不应发生,真实核心库数据),
      // 退回手动描述,不让这条与移动端布局无关的偶发情况挡住本用例。
      await matchInput.fill("站外赛事 手动录入 A vs B");
    }
    const selectionInput = page.locator('input[placeholder="选项(如 主胜)"]');
    if ((await selectionInput.count()) > 0 && !(await selectionInput.inputValue())) {
      await selectionInput.fill("主胜");
      await page.locator('input[placeholder="赔率"]').fill("1.95");
    }
    await expectNoHorizontalScroll(page, vp.width);

    const titleInput = page.locator('input[placeholder="如:今日三串一"]');
    await titleInput.fill(`移动端验证-${vp.label}`);
    await expectNoHorizontalScroll(page, vp.width);

    await page.getByRole("button", { name: "创建草稿" }).click();
    await expect(page.getByText(`移动端验证-${vp.label}`)).toBeVisible({ timeout: 10_000 });
    await expectNoHorizontalScroll(page, vp.width);

    const card = page
      .locator('div[class*="rowCard"]')
      .filter({ hasText: `移动端验证-${vp.label}` });
    await expect(card).toBeVisible();

    // 会员预览
    await card.getByRole("button", { name: "会员预览" }).click();
    await expect(card.getByTestId("preview-member-view")).toBeVisible({ timeout: 10_000 });
    await expectNoHorizontalScroll(page, vp.width);
    await card.getByRole("button", { name: "关闭预览" }).click();

    // 编辑
    await card.getByRole("button", { name: "编辑", exact: true }).click();
    await expect(card.getByTestId("edit-panel")).toBeVisible();
    await expectNoHorizontalScroll(page, vp.width);
    await card.getByRole("button", { name: "取消", exact: true }).click();

    // 发布:必须先弹原生 confirm 二次确认,取消后不应该真的发布成功。
    let dialogSeen = false;
    page.once("dialog", async (dialog) => {
      dialogSeen = true;
      expect(dialog.type()).toBe("confirm");
      await dialog.dismiss();
    });
    await card.getByRole("button", { name: "发布" }).click();
    await page.waitForTimeout(300);
    expect(dialogSeen).toBe(true);
    // 取消后这张卡片自身的状态徽标仍是「草稿」,不应该变成「已发布」
    // (用 card 限定作用域——筛选下拉本身就有一个字面量是「已发布」的
    // <option>,不能拿全页文本判断)。
    await expect(card.getByText("已发布", { exact: true })).toHaveCount(0);

    // 再次点击并接受确认——E2E 的 odds.db 是空表,手动录入/未选真实盘口的
    // 腿会被后端拒绝发布,验证具体错误文案在窄屏下完整展示、不产生横向滚动。
    page.once("dialog", (dialog) => dialog.accept());
    await card.getByRole("button", { name: "发布" }).click();
    await expect(
      card.getByText(/已发布|缺乏真实盘口溯源/).first(),
    ).toBeVisible({ timeout: 10_000 });
    await expectNoHorizontalScroll(page, vp.width);
  });
}
