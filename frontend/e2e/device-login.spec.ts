import { execFileSync } from "node:child_process";
import { test, expect, type APIRequestContext } from "@playwright/test";
import type { PostJson } from "../lib/api-v1";
import { API, e2ePlatformDbPath, sendCodeViaWebhook } from "./helpers";

/**
 * 公众号发码登录完整 E2E(CLAUDE.md §7.3,2026-10 起):
 * 网页获取 4 位验证码 → 模拟微信服务器把"用户发给公众号的验证码"推到 webhook 批准
 * → 网页轮询原子领取 → 会话生效(刷新仍登录)→ 二次 claim 410。
 * 另覆盖:伪造签名 403 且不产生批准副作用、发错验证码不登录、错误 secret 403、
 * 篡改过期(直接 SQL 改 data/e2e 临时库)410、新用户首次登录起昵称。
 *
 * 种子用户 mock-openid-user-1 显示名 E2E会员(老用户,已设过昵称,不弹起昵称框)。
 */

/** 从生成类型派生(Pydantic 单一真源,宪法 §10.3),不手写与 API 响应重复的 type。 */
type DeviceCreateResponse = PostJson<"/api/v1/auth/wechat/device">;

async function createDeviceRequest(
  request: APIRequestContext,
): Promise<DeviceCreateResponse> {
  const r = await request.post(`${API}/api/v1/auth/wechat/device`, { data: {} });
  expect(r.status()).toBe(200);
  return (await r.json()) as DeviceCreateResponse;
}

test("完整验证码登录:网页取码→发码给公众号→网页轮询→会话生效→二次 claim 410", async ({
  browser,
}) => {
  const desktop = await browser.newContext();
  const page = await desktop.newPage();

  const deviceRespPromise = page.waitForResponse(
    (r) => r.url().endsWith("/api/v1/auth/wechat/device") && r.request().method() === "POST",
  );
  await page.goto("/login?next=/account");
  const device = (await (await deviceRespPromise).json()) as DeviceCreateResponse;

  // 页面上显示的就是这次请求的 4 位验证码
  expect(device.login_code).toMatch(/^\d{4}$/);
  const codeEl = page.getByTestId("login-code");
  await expect(codeEl).toBeVisible();
  await expect(codeEl).toHaveText(device.login_code);
  await expect(page.getByText("足球喵喵第").first()).toBeVisible();

  // secret 只留在浏览器内存:不出现在 DOM
  expect(await page.content()).not.toContain(device.secret);

  // 伪造签名的 webhook 投递必须 403,且不产生批准副作用
  const forged = await sendCodeViaWebhook(page.request, device.login_code, "mock-openid-user-1", {
    signature: "0".repeat(40),
  });
  expect(forged.status()).toBe(403);

  // 发一个不是 4 位数字的消息:回复"验证码错误",不登录
  const notCode = await sendCodeViaWebhook(page.request, "你好");
  expect(notCode.status()).toBe(200);
  expect(await notCode.text()).toContain("验证码错误，请重新输入");

  // 发对验证码 → 批准
  const ok = await sendCodeViaWebhook(page.request, device.login_code);
  expect(ok.status()).toBe(200);
  expect(await ok.text()).toContain("登录成功");

  // 网页轮询领取成功 → 跳转 next=/account(老用户不弹起昵称框)
  await page.waitForURL("**/account", { timeout: 20_000 });
  await expect(page.getByText("E2E会员").first()).toBeVisible();

  // 刷新后仍是已登录态(opaque session cookie 持久化)
  await page.reload();
  await expect(page.getByText("E2E会员").first()).toBeVisible();

  // 同一 request 二次 claim:已原子消费 → 410
  const second = await page.request.post(
    `${API}/api/v1/auth/wechat/device/${device.request_id}/claim`,
    { data: { secret: device.secret } },
  );
  expect(second.status()).toBe(410);

  // 用过的验证码再发一次:无效
  const reused = await sendCodeViaWebhook(page.request, device.login_code);
  expect(await reused.text()).toContain("验证码无效或已过期");

  await desktop.close();
});

test("新用户首次登录:起昵称后跳转,账户页显示昵称与用户编号", async ({ browser }) => {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  const openid = `e2e-new-user-${Date.now()}`;

  const deviceRespPromise = page.waitForResponse(
    (r) => r.url().endsWith("/api/v1/auth/wechat/device") && r.request().method() === "POST",
  );
  await page.goto("/login?next=/account");
  const device = (await (await deviceRespPromise).json()) as DeviceCreateResponse;

  const ok = await sendCodeViaWebhook(page.request, device.login_code, openid);
  expect(await ok.text()).toContain("登录成功");

  // 新账号:跳转前先起昵称
  const input = page.getByTestId("nickname-input");
  await expect(input).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("nickname-save")).toBeDisabled();
  await input.fill("测试球迷小王");
  await page.getByTestId("nickname-save").click();

  await page.waitForURL("**/account", { timeout: 20_000 });
  await expect(page.getByText("测试球迷小王").first()).toBeVisible();
  const me = (await (await page.request.get(`${API}/api/v1/me`)).json()) as {
    user: { short_code: string; nickname_set: boolean };
  };
  expect(me.user.nickname_set).toBe(true);
  await expect(page.getByText(me.user.short_code).first()).toBeVisible();

  await ctx.close();
});

test("验证码登录安全边界:错误 secret 403(不烧毁请求)/ 篡改过期后 claim 410", async ({
  request,
}) => {
  // 错误 secret → 403;正确 secret 仍可领取(错误尝试不烧毁请求)
  const reqA = await createDeviceRequest(request);
  const sendA = await sendCodeViaWebhook(request, reqA.login_code);
  expect(sendA.status()).toBe(200);
  const wrong = await request.post(
    `${API}/api/v1/auth/wechat/device/${reqA.request_id}/claim`,
    { data: { secret: "wrong-secret" } },
  );
  expect(wrong.status()).toBe(403);
  const right = await request.post(
    `${API}/api/v1/auth/wechat/device/${reqA.request_id}/claim`,
    { data: { secret: reqA.secret } },
  );
  expect(right.status()).toBe(200);
  expect(((await right.json()) as { status: string }).status).toBe("claimed");

  // 新建 request,直接 SQL 篡改 E2E 临时库的 expires_at(绝不触真实库)→ claim 410
  const reqB = await createDeviceRequest(request);
  execFileSync("sqlite3", [
    e2ePlatformDbPath(),
    "PRAGMA busy_timeout=5000; " +
      "UPDATE device_login_requests SET expires_at='2020-01-01T00:00:00Z' " +
      `WHERE id='${reqB.request_id}'`,
  ]);
  const expired = await request.post(
    `${API}/api/v1/auth/wechat/device/${reqB.request_id}/claim`,
    { data: { secret: reqB.secret } },
  );
  expect(expired.status()).toBe(410);
});
