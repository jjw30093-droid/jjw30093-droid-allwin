import { createHash, randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { APIRequestContext } from "@playwright/test";

export const API = "http://127.0.0.1:8010";

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
