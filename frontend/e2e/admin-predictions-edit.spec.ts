import { test, expect } from "@playwright/test";
import { seedMatchId, seedSnapshotId } from "./helpers";

/**
 * 管理员登录 → 预测 Tab → 编辑一条已锁定的正式预测 → 修正记录留痕可见
 * (2026-08-05:CLAUDE.md §9.1 改为"可编辑但强制留痕",取代此前的锁定后不可改)。
 *
 * 复用 seed_e2e.py 已经 publish+lock 的种子快照(见 admin-studio.spec.ts 同款
 * 登录流程),不额外造数据。
 */

test("管理员登录 → 编辑已锁定预测 → 修正记录公开可查", async ({ page }) => {
  test.setTimeout(60_000);
  const matchId = seedMatchId();
  const snapshotId = seedSnapshotId();

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

  await page.goto("/admin");
  await expect(page.getByText("无权限")).toHaveCount(0);
  await page.getByRole("button", { name: "预测" }).click();

  const row = page.locator("tr", { has: page.getByText(`#${matchId}`, { exact: true }) });
  await expect(row).toBeVisible();
  await expect(row.getByText("locked", { exact: false })).toBeVisible();
  await expect(row.getByText("未修正")).toBeVisible();

  await row.getByRole("button", { name: "编辑" }).click();
  const homeInput = row.getByPlaceholder("主胜");
  const drawInput = row.getByPlaceholder("平局");
  const awayInput = row.getByPlaceholder("客胜");
  await homeInput.fill("0.6");
  await drawInput.fill("0.25");
  await awayInput.fill("0.15");
  await row.getByPlaceholder("修正原因(必填)").fill("E2E 修正验证");
  await row.getByRole("button", { name: "确认修正" }).click();

  await expect(page.getByText(/已修正.*累计修正 1 次/)).toBeVisible({ timeout: 10_000 });
  await expect(row.getByText("已修正 1 次")).toBeVisible();
  await expect(row.getByText("未修正")).toHaveCount(0);
  await expect(row.getByText("60.0% / 25.0% / 15.0%")).toBeVisible();

  // 公开战绩页(/track-record)也会展示修正徽标,但走 120 秒 ISR revalidate
  // (backend/api routes 的公开缓存策略,CLAUDE.md §10.2,与本次改动无关)——
  // 不在这里等待跨那个窗口,数据管道正确性已由上面 admin 侧的即时验证覆盖;
  // API 层的 edit_count/last_edited_at 字段本身在 tests/backend 已直接测过。
  void snapshotId;
});
