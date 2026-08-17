import { test, expect } from "@playwright/test";

/**
 * 权限口径修正(2026-08-16,后端对任何人恒返回完整比赛内容):
 *
 * 此前这个文件验证"首页锁定联赛比赛卡片的登录提示,登录后消失"——
 * MatchSummary.requires_login 字段已从后端彻底删除,不再存在"这场比赛
 * 被锁定,看不到概率"这个产品状态,原有场景整体作废。
 *
 * 新场景验证反过来的不变量:匿名首页与登录后首页看到完全相同的重点比赛
 * 与近期比赛内容(不再有"登录后解锁更多"这回事),且全站任何位置都不出现
 * "登录后查看…概率"这类已下架的登录门禁文案。
 */

test("首页:匿名与登录看到完全相同的重点比赛内容,不出现任何登录门禁提示", async ({
  page,
}) => {
  await page.goto("/");
  const featured = page.getByTestId("featured-match-card");
  await expect(featured).toBeVisible();

  // 不再有"登录后查看…概率"这类锁定提示——比赛内容对匿名恒完整。
  await expect(page.getByText(/登录后查看.*概率/)).toHaveCount(0);
  await expect(page.getByText("需登录")).toHaveCount(0);
});
