import { execFileSync } from "node:child_process";
import { test, expect, type APIRequestContext } from "@playwright/test";
import type { PostJson } from "../lib/api-v1";
import { API, e2ePlatformDbPath } from "./helpers";

/**
 * Device Login 完整 E2E(CLAUDE.md §7.3):
 * 桌面创建 → 真二维码(canvas)→ 手机(独立 context)扫码走 Mock OAuth 批准
 * → 桌面轮询原子领取 → 会话生效(刷新仍登录)→ 二次 claim 410。
 * 另覆盖:错误 secret 403、篡改过期(直接 SQL 改 data/e2e 临时库)410。
 *
 * MockWechatProvider.authorize_url 固定 code=mock-user-1,
 * 种子已给 mock-openid-user-1 绑定 pro 会员(显示名 E2E会员)。
 */

/** 从生成类型派生(Pydantic 单一真源,宪法 §10.3),不再手写与 API 响应重复的 type。 */
type DeviceCreateResponse = PostJson<"/api/v1/auth/wechat/device">;

async function createDeviceRequest(
  request: APIRequestContext,
): Promise<DeviceCreateResponse> {
  const r = await request.post(`${API}/api/v1/auth/wechat/device`, { data: {} });
  expect(r.status()).toBe(200);
  return (await r.json()) as DeviceCreateResponse;
}

/** 等价"手机扫码":直接打开 qr_url,mock provider 302 回 callback 完成批准。 */
async function approveViaQrUrl(request: APIRequestContext, qrUrl: string) {
  const r = await request.get(qrUrl);
  expect(r.status()).toBe(200);
  expect(((await r.json()) as { status: string }).status).toBe("approved");
}

test("完整 Device Login:桌面创建→真二维码→手机批准→桌面轮询→会话生效→二次 claim 410", async ({
  browser,
}) => {
  const desktop = await browser.newContext();
  const desktopPage = await desktop.newPage();

  // 捕获 device 创建响应(request_id/secret 供后续二次 claim 断言)
  const deviceRespPromise = desktopPage.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/auth/wechat/device") &&
      r.request().method() === "POST",
  );
  await desktopPage.goto("/login?next=/account");
  const device = (await (
    await deviceRespPromise
  ).json()) as DeviceCreateResponse;

  // 真二维码:页面渲染 <canvas>,DOM 可提取 qr_url
  const canvas = desktopPage.locator("canvas[data-qr-url]");
  await expect(canvas).toBeVisible();
  const qrUrl = await canvas.getAttribute("data-qr-url");
  expect(qrUrl).toBe(device.qr_url);
  expect(qrUrl!).toContain(`${API}/api/v1/auth/wechat/oa/start?device=`);
  expect(qrUrl!).toContain(device.request_id);

  // canvas 真的画了二维码(存在深色模块,不是空白画布)
  const drawn = await canvas.evaluate((el) => {
    const c = el as HTMLCanvasElement;
    const ctx = c.getContext("2d");
    if (!ctx || c.width === 0 || c.height === 0) return false;
    const data = ctx.getImageData(0, 0, c.width, c.height).data;
    for (let i = 0; i < data.length; i += 4) {
      if (data[i] < 128) return true;
    }
    return false;
  });
  expect(drawn).toBe(true);

  // secret 只留在浏览器内存:不进二维码 URL,也不出现在 DOM
  expect(device.qr_url).not.toContain(device.secret);
  expect(await desktopPage.content()).not.toContain(device.secret);

  // 手机 context(独立 cookie 空间)打开 qr_url,走 Mock OAuth 完成批准
  const phone = await browser.newContext();
  const phonePage = await phone.newPage();
  await phonePage.goto(qrUrl!);
  await expect(phonePage.locator("body")).toContainText("approved");
  // 批准动作不给手机端种网站会话 cookie
  const phoneCookies = await phone.cookies(API);
  expect(
    phoneCookies.find((c) => c.name === "allwin_session"),
  ).toBeUndefined();

  // 桌面轮询领取成功 → 跳转 next=/account,已登录(种子 pro 会员)
  await desktopPage.waitForURL("**/account", { timeout: 20_000 });
  await expect(desktopPage.getByText("E2E会员").first()).toBeVisible();

  // 刷新后仍是已登录态(opaque session cookie 持久化)
  await desktopPage.reload();
  await expect(desktopPage.getByText("E2E会员").first()).toBeVisible();

  // 同一 request 二次 claim:已原子消费 → 410
  const second = await desktopPage.request.post(
    `${API}/api/v1/auth/wechat/device/${device.request_id}/claim`,
    { data: { secret: device.secret } },
  );
  expect(second.status()).toBe(410);

  await phone.close();
  await desktop.close();
});

test("Device Login 安全边界:错误 secret 403(不烧毁请求)/ 篡改过期后 claim 410", async ({
  request,
}) => {
  // 错误 secret → 403;正确 secret 仍可领取(错误尝试不烧毁请求)
  const reqA = await createDeviceRequest(request);
  await approveViaQrUrl(request, reqA.qr_url);
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
