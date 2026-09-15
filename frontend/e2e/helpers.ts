import { createHash, randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, type APIRequestContext, type Page } from "@playwright/test";

export const API = "http://127.0.0.1:8010";

/**
 * 账号密码登录(5 个 admin/studio 用例共用)。
 *
 * 2026-09-16 收口:此前 5 个 spec 各抄一份
 * `getByText("管理员密码登录").click()` + 点不开就再点一次的重试。这个写法
 * 有两处脆弱:① 文案一改就全找不到;② 登录页按微信扫码开没开放,密码登录
 * 有两种形态——没开放时是常驻主卡片(**没有可点的 summary**),开放时才是
 * 折叠的次要入口,盲点一次会把已经展开的表单反向收起来。
 *
 * 这里改成"看表单在不在,不在才去展开",两种形态都走得通。
 */
export async function loginWithPassword(
  page: Page,
  username: string,
  password: string,
) {
  await page.goto("/login");
  const usernameField = page.getByLabel("用户名");
  if (!(await usernameField.isVisible().catch(() => false))) {
    await page.getByText("账号密码登录").click();
  }
  await expect(usernameField).toBeVisible({ timeout: 3000 });
  await usernameField.fill(username);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
}

/** webhook 签名 Token(development 默认值,backend/auth/config.py)。 */
const DEV_WEBHOOK_TOKEN = "dev-webhook-token";

/** 模拟微信服务器投递带参二维码扫码事件(SCAN):按共享 Token 计算合法签名。
 * webhook 入站链路不依赖 Provider,E2E 走的就是生产同一条代码路径。 */
export async function approveViaWebhook(
  request: APIRequestContext,
  requestId: string,
  openid = "mock-openid-user-1",
) {
  const timestamp = String(Math.floor(Date.now() / 1000));
  const nonce = randomUUID().replace(/-/g, "");
  const signature = createHash("sha1")
    .update([DEV_WEBHOOK_TOKEN, timestamp, nonce].sort().join(""))
    .digest("hex");
  const xml =
    "<xml>" +
    "<ToUserName><![CDATA[gh_mock_oa]]></ToUserName>" +
    `<FromUserName><![CDATA[${openid}]]></FromUserName>` +
    `<CreateTime>${timestamp}</CreateTime>` +
    "<MsgType><![CDATA[event]]></MsgType>" +
    "<Event><![CDATA[SCAN]]></Event>" +
    `<EventKey><![CDATA[${requestId}]]></EventKey>` +
    "</xml>";
  const r = await request.post(
    `${API}/api/v1/auth/wechat/webhook?signature=${signature}&timestamp=${timestamp}&nonce=${nonce}`,
    { data: xml, headers: { "Content-Type": "application/xml" } },
  );
  return r;
}

/** 行首锚定读取 seed_info.txt(edit_match_id 等行含 match_id= 子串,不能裸搜)。 */
function seedInfoValue(key: string): string {
  const txt = readFileSync(
    resolve(__dirname, "../../data/e2e/seed_info.txt"),
    "utf-8",
  );
  const m = txt.match(new RegExp(`^${key}=(\\S+)$`, "m"));
  if (!m) throw new Error(`seed_info.txt 缺 ${key}`);
  return m[1];
}

/** 种子信息(webServer 启动命令里 seed_e2e 先跑,测试执行时文件必已存在)。 */
export function seedMatchId(): number {
  return Number(seedInfoValue("match_id"));
}

/** 种子信息里的已锁定正式预测 id(seed_e2e.py 已 publish+lock)。 */
export function seedSnapshotId(): string {
  return seedInfoValue("snapshot_id");
}

/**
 * admin-predictions-edit.spec.ts 专用的隔离编辑目标(首页 7 天窗口之外)。
 * 编辑用例按字母序先于 anonymous/auth 跑且会把概率改成 0.6/0.25/0.15,
 * 绝不能复用上面 48% 断言依赖的主种子快照。
 */
export function seedEditMatchId(): number {
  return Number(seedInfoValue("edit_match_id"));
}

export function seedEditSnapshotId(): string {
  return seedInfoValue("edit_snapshot_id");
}

/** E2E 临时 platform 库路径(仅供测试篡改行;指向 data/e2e,绝不指向真实 data/platform.db)。 */
export function e2ePlatformDbPath(): string {
  return resolve(__dirname, "../../data/e2e/platform.db");
}
