// 抖音安全区自查：对给定 html 文件，取出所有"信息元素"(带可见文字的叶子
// 节点 + <img>)的 boundingBox，跟三块遮挡区(顶/底/右侧按钮列)做相交检测，
// 列出任何相交的元素——正式验收要求这份清单是空的。
// 背景装饰(.watermark、safezone-debug 自己的覆盖层)不算信息元素，排除。
const path = require("path");
const { chromium } = require(
  "/Users/wanglujun/projects/all-win/frontend/node_modules/@playwright/test"
);

const ZONES = {
  top: { x0: 0, y0: 0, x1: 1080, y1: 240 },
  bottom: { x0: 0, y0: 1440, x1: 1080, y1: 1920 },
  right: { x0: 900, y0: 880, x1: 1080, y1: 1920 },
};

function intersects(r, z) {
  return r.x < z.x1 && r.x + r.width > z.x0 && r.y < z.y1 && r.y + r.height > z.y0;
}

async function main() {
  const [, , htmlPath] = process.argv;
  if (!htmlPath) {
    console.error("usage: node check_safezone.cjs <html_path>");
    process.exit(1);
  }
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1080, height: 1920 } });
  await page.goto("file://" + path.resolve(htmlPath));
  await page.waitForLoadState("networkidle");

  const elements = await page.evaluate(() => {
    function isVisible(el) {
      const cs = getComputedStyle(el);
      return cs.display !== "none" && cs.visibility !== "hidden" && parseFloat(cs.opacity) > 0.02;
    }
    const out = [];
    const all = document.querySelectorAll("body *");
    for (const el of all) {
      if (el.closest(".watermark, .safezone-debug")) continue;
      if (!isVisible(el)) continue;
      const isImg = el.tagName === "IMG";
      const hasOwnText = [...el.childNodes].some(
        (n) => n.nodeType === 3 && n.textContent.trim().length > 0
      );
      if (!isImg && !hasOwnText) continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      out.push({
        tag: el.tagName,
        cls: el.className && typeof el.className === "string" ? el.className : "",
        text: (el.textContent || "").trim().slice(0, 24),
        rect: { x: r.x, y: r.y, width: r.width, height: r.height },
      });
    }
    return out;
  });

  const hits = [];
  for (const el of elements) {
    for (const [zoneName, z] of Object.entries(ZONES)) {
      if (intersects(el.rect, z)) {
        hits.push({ zone: zoneName, ...el });
      }
    }
  }

  console.log(`[${path.basename(htmlPath)}] 信息元素总数: ${elements.length}`);
  if (hits.length === 0) {
    console.log("  与遮挡区相交的元素: 无");
  } else {
    console.log(`  与遮挡区相交的元素: ${hits.length} 个`);
    for (const h of hits) {
      console.log(`    [${h.zone}] <${h.tag} class="${h.cls}"> "${h.text}" rect=${JSON.stringify(h.rect)}`);
    }
  }

  await browser.close();
  process.exit(hits.length > 0 ? 1 : 0);
}

main().catch((err) => {
  console.error(err);
  process.exit(2);
});
