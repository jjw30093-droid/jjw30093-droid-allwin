import { test, expect, type Download } from "@playwright/test";
import { readFileSync } from "node:fs";

/**
 * P1:Studio 图片导出真实验收。
 * 不满足于后台 export_jobs 审计记录——下载真实 PNG 文件,校验:
 * 1) PNG signature;2) IHDR 实际宽高(1080×1920 / 1080×1350);3) 文件大小下限;
 * 4) 场景卡内含数据截止与模型版本;5) JSON/SRT 服务端导出仍可用。
 */

const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const MIN_PNG_BYTES = 20_000;

async function readDownload(dl: Download): Promise<Buffer> {
  const path = await dl.path();
  if (!path) throw new Error("download 没有落盘路径");
  return readFileSync(path);
}

function pngDimensions(buf: Buffer): { width: number; height: number } {
  // IHDR 固定在 signature(8B)+ length(4B)+ "IHDR"(4B) 之后:宽高各 4 字节大端
  expect(buf.subarray(0, 8).equals(PNG_SIGNATURE), "PNG signature 不匹配").toBe(true);
  expect(buf.subarray(12, 16).toString("ascii")).toBe("IHDR");
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) };
}

test("Studio 导出 PNG:signature + 实际像素尺寸 + JSON/SRT", async ({ page }) => {
  test.setTimeout(180_000);

  // 管理员密码登录(种子 e2e-admin)
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

  // 进入 Studio 编辑器(创建或打开草稿)
  await page.goto("/studio");
  await page.getByRole("button", { name: /创建草稿|打开草稿/ }).first().click();
  await page.waitForURL("**/studio/matches/**");
  await expect(page.getByText("导出").first()).toBeVisible();

  // 场景卡内容:数据截止 + 模型版本(导出画面自带,防"文件名对了内容没带")
  await expect(page.getByText(/数据截止/).first()).toBeVisible();
  await expect(page.getByText(/模型版本/).first()).toBeVisible();

  // ── PNG 1080×1920 ──
  const dl1920 = page.waitForEvent("download", { timeout: 90_000 });
  await page.getByRole("button", { name: "PNG 当前场景 1080×1920" }).click();
  const buf1920 = await readDownload(await dl1920);
  expect(buf1920.length).toBeGreaterThan(MIN_PNG_BYTES);
  expect(pngDimensions(buf1920)).toEqual({ width: 1080, height: 1920 });

  // ── PNG 1080×1350 ──
  const dl1350 = page.waitForEvent("download", { timeout: 90_000 });
  await page.getByRole("button", { name: "PNG 当前场景 1080×1350" }).click();
  const buf1350 = await readDownload(await dl1350);
  expect(buf1350.length).toBeGreaterThan(MIN_PNG_BYTES);
  expect(pngDimensions(buf1350)).toEqual({ width: 1080, height: 1350 });

  // ── JSON / SRT 服务端导出仍可用(TXT 已在 admin-studio.spec 覆盖) ──
  const dlJson = page.waitForEvent("download", { timeout: 60_000 });
  await page.getByRole("button", { name: "JSON" }).click();
  const jsonBuf = await readDownload(await dlJson);
  const parsed = JSON.parse(jsonBuf.toString("utf-8"));
  expect(parsed).toHaveProperty("bundle");

  const dlSrt = page.waitForEvent("download", { timeout: 60_000 });
  await page.getByRole("button", { name: /SRT/ }).click();
  const srtText = (await readDownload(await dlSrt)).toString("utf-8");
  expect(srtText).toMatch(/\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}/);
});
