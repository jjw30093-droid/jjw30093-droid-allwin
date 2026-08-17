import { test, expect } from "@playwright/test";

/**
 * 权限口径修正(2026-08-16,后端对任何人恒返回完整比赛内容):
 *
 * 此前这个文件验证"匿名可见但锁定小联赛概率,登录后按真实权益解锁"——
 * LeagueInfo.accessible/requires_login 字段已从后端彻底删除,联赛筛选栏
 * 的"登录"角标与 MatchRow 的"登录后查看胜平负概率"提示都已下架,原有
 * 场景整体作废。
 *
 * 新场景验证反过来的不变量:任何联赛(含此前的 league:lottery 档,如日职联)
 * 匿名筛选时,联赛筛选 chip 不带"登录"角标,比赛行也不出现登录提示。
 */

test("比赛列表:任何联赛(含此前需登录的小联赛)筛选栏和比赛行都不出现登录门禁提示", async ({
  page,
}) => {
  await page.goto("/matches?league=223&status=upcoming&window=all");

  const chip = page.getByRole("link", { name: "日职联" });
  await expect(chip).toBeVisible();
  // exact 精确匹配"日职联"(不带任何角标文字)。
  await expect(page.getByRole("link", { name: "日职联", exact: true })).toBeVisible();
  await expect(page.getByText("登录后查看胜平负概率")).toHaveCount(0);
});
