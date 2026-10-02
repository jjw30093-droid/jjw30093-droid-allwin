import { test, expect } from "@playwright/test";
import { API, sendCodeViaWebhook } from "./helpers";

/**
 * 登录门禁(2026-10-02,经站长批准):未登录只能看英超的比赛详情与联赛数据。
 * 列表里看得到非英超比赛(对阵/时间/概率),点进去直接到登录页,登录后回到原页面。
 * 防泄漏:未登录拿到的 HTML/RSC 与 API 响应里不含非英超的详情数据(赔率快照等)。
 */

async function nonEplMatchId(request: import("@playwright/test").APIRequestContext): Promise<number> {
  const r = await request.get(`${API}/api/v1/matches?status=finished&window=all&limit=200`);
  expect(r.ok()).toBeTruthy();
  const m = (await r.json()).matches.find((x: { league_id: number }) => x.league_id !== 47);
  expect(m, "种子库里需要至少一场非英超比赛").toBeTruthy();
  return m.match_id as number;
}

test("未登录打开非英超比赛 → 登录页;登录后回到原比赛页", async ({ page }) => {
  const id = await nonEplMatchId(page.request);

  // 防泄漏:API 401 且只带公开赛事标识;服务端渲染的 HTML 不含详情数据
  for (const sub of ["", "/odds", "/report", "/preview", "/markets"]) {
    const r = await page.request.get(`${API}/api/v1/matches/${id}${sub}`);
    expect(r.status(), sub).toBe(401);
    expect((await r.json()).code).toBe("login_required");
  }
  const html = await (await page.request.get(`/matches/${id}`)).text();
  for (const leaked of ["snapshots", "payload", "home_form", "summary_points", "market_phase"]) {
    expect(html, leaked).not.toContain(leaked);
  }

  const deviceRespPromise = page.waitForResponse(
    (r) => r.url().endsWith("/api/v1/auth/wechat/device") && r.request().method() === "POST",
  );
  await page.goto(`/matches/${id}`);
  await page.waitForURL(/\/login\?next=/);
  expect(decodeURIComponent(new URL(page.url()).searchParams.get("next") ?? "")).toBe(`/matches/${id}`);

  const device = (await (await deviceRespPromise).json()) as { login_code: string };
  expect((await sendCodeViaWebhook(page.request, device.login_code, "mock-openid-gate")).status()).toBe(200);
  await page.waitForURL(`**/matches/${id}`, { timeout: 20_000 });
  await expect(page.getByLabel("比赛详情加载中")).toHaveCount(0, { timeout: 15_000 });
  await expect(page.getByText("数据暂时无法加载")).toHaveCount(0);
});

test("未登录打开非英超联赛页 → 登录页;英超联赛页照常", async ({ page }) => {
  await page.goto("/league/47/standings");
  await expect(page).toHaveURL(/\/league\/47\/standings$/);
  await expect(page.getByText("英超 · 排名榜")).toBeVisible();

  await page.goto("/league/87/standings");
  await page.waitForURL(/\/login\?next=/);
  expect(decodeURIComponent(new URL(page.url()).searchParams.get("next") ?? "")).toBe("/league/87/standings");
});

test("比赛列表对未登录仍列出非英超比赛", async ({ page }) => {
  await page.goto("/matches?status=finished&window=all");
  const r = await page.request.get(`${API}/api/v1/matches?status=finished&window=all&limit=200`);
  const leagues = new Set((await r.json()).matches.map((m: { league_id: number }) => m.league_id));
  expect([...leagues].some((l) => l !== 47)).toBe(true);
});
