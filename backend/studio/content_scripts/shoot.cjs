// 用项目已有的 frontend/node_modules/@playwright/test（e2e 测试同一份浏览器
// 二进制，不额外装一份 python playwright + chromium）把渲染好的静态 HTML
// 截图成 PNG。viewport 按调用方传入的宽高，deviceScaleFactor=2 拿到 2x 高清
// 位图，缩小到最终 1x 尺寸的最后一步在 Python 侧用 PIL 做（见 render_series.py）。
// 宽高做成参数而不是写死 1080x1440，是这次加抖音 1080x1920 画布之后的改动——
// 小红书和抖音共用这一份截图脚本，不重复一份。
const path = require("path");
const { chromium } = require(
  "/Users/wanglujun/projects/all-win/frontend/node_modules/@playwright/test"
);

async function main() {
  const [, , htmlPath, outPath, wArg, hArg] = process.argv;
  const width = parseInt(wArg || "1080", 10);
  const height = parseInt(hArg || "1440", 10);
  if (!htmlPath || !outPath) {
    console.error("usage: node shoot.cjs <html_path> <out_png_path> [width] [height]");
    process.exit(1);
  }
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width, height },
    deviceScaleFactor: 2,
  });
  await page.goto("file://" + path.resolve(htmlPath));
  // networkidle 等页面里热链的远程图（如姆巴佩头像 FotMob CDN）真正加载完，
  // 不只是等本地 @font-face 字体——纯本地页面这个 wait 几乎立即通过，不额外费时。
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(150); // 再等一帧，确保变体字体渲染稳定
  await page.screenshot({ path: outPath });
  await browser.close();
  console.log(`WROTE ${outPath}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
