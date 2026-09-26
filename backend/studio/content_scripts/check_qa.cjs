// B费 NO.02 抖音版 + 小红书P2 自动QA检测。对给定html文件跑以下检查：
// 1. 元素两两 boundingBox 相交(同一批可见文字/图片元素互相重叠)
// 2. 元素与抖音三块遮挡区相交(仅douyin平台传safezone)
// 3. 最小字号(<22px报警，沿用第一版规则里最严的一条)
// 4. 内容区纵向连续空白是否超过80px
// 5. 头像放大倍数(相对192px源图1.5倍上限)
// 6. 文本黑名单：下划线字符、fact_、p90、网址、竞彩、投注、赔率
// 7. 省略号(...或…)——出现即报警，说明有文字被截断
const path = require("path");
const { chromium } = require(
  "/Users/wanglujun/projects/all-win/frontend/node_modules/@playwright/test"
);

const DOUYIN_ZONES = {
  top: { x0: 0, y0: 0, x1: 1080, y1: 240 },
  bottom: { x0: 0, y0: 1440, x1: 1080, y1: 1920 },
  right: { x0: 900, y0: 880, x1: 1080, y1: 1920 },
};
const BLACKLIST = ["_", "fact_", "p90", "http://", "https://", "www.", "竞彩", "投注", "赔率"];
const ELLIPSIS = ["...", "…"];

function rectsIntersect(a, b) {
  return a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
}
function zoneIntersect(r, z) {
  return r.x < z.x1 && r.x + r.width > z.x0 && r.y < z.y1 && r.y + r.height > z.y0;
}

async function main() {
  const [, , htmlPath, platform] = process.argv; // platform: xhs|douyin
  const browser = await chromium.launch();
  const vw = platform === "douyin" ? 1080 : 1080;
  const vh = platform === "douyin" ? 1920 : 1440;
  const page = await browser.newPage({ viewport: { width: vw, height: vh } });
  await page.goto("file://" + path.resolve(htmlPath));
  await page.waitForLoadState("networkidle");

  const result = await page.evaluate((platform) => {
    function isVisible(el) {
      const cs = getComputedStyle(el);
      return cs.display !== "none" && cs.visibility !== "hidden" && parseFloat(cs.opacity) > 0.02;
    }
    // ---- 文字/图片元素清单(排除水印/安全区调试层)，每个元素配一个自增id
    //    并记录自己的路径(用于之后判断祖先/后代关系，比面积比例heuristic
    //    可靠——只要是同一条DOM链上的父子/祖孙，天然"包含"不算版式冲突，
    //    不管子元素面积占多大比例，例如整行文字里单独标红的一个数字span)。 ----
    const elements = [];
    let uid = 0;
    const withId = new Map();
    function pushEl(el, tag, cls, text, r, fontSize, isImg) {
      const id = uid++;
      withId.set(el, id);
      elements.push({ id, el, tag, cls, text, rect: { x: r.x, y: r.y, width: r.width, height: r.height }, fontSize, isImg });
    }
    document.querySelectorAll("body *").forEach((el) => {
      if (el.closest(".watermark, .safezone-debug")) return;
      if (!isVisible(el)) return;
      const isImg = el.tagName === "IMG";
      const hasOwnText = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim().length > 0);
      if (!isImg && !hasOwnText) return;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return;
      const cs = getComputedStyle(el);
      pushEl(el, el.tagName, typeof el.className === "string" ? el.className : "",
        (el.textContent || "").trim().slice(0, 40), r, parseFloat(cs.fontSize) || null, isImg);
    });
    document.querySelectorAll("svg text, svg image").forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return;
      const cs = getComputedStyle(el);
      pushEl(el, el.tagName, "", (el.textContent || "").trim().slice(0, 40), r,
        parseFloat(cs.fontSize) || null, el.tagName === "image");
    });

    // ---- 头像放大倍数：找 .avatar-wrap img.face / SVG clip 头像 / player_badge_avatar 的 img（排除队徽 badge） ----
    const avatarChecks = [];
    document.querySelectorAll("img").forEach((img) => {
      const r = img.getBoundingClientRect();
      if (r.width === 0) return;
      const src = img.getAttribute("src") || "";
      if (src.includes("/avatars/")) {
        avatarChecks.push({ src, displayW: r.width, displayH: r.height });
      }
    });
    document.querySelectorAll("svg image").forEach((im) => {
      const href = im.getAttribute("href") || "";
      if (href.includes("/avatars/")) {
        const w = parseFloat(im.getAttribute("width")) || 0;
        avatarChecks.push({ src: href, displayW: w, displayH: w, svgNative: true });
      }
    });

    // ---- 两两相交，在页面内直接用 el.contains() 判祖先/后代关系并跳过——
    //    比面积比例heuristic可靠：一段文字里单独标红的一个数字span，面积
    //    占比可能远高于8%，但它就是父元素自己的一部分，不算版式冲突。 ----
    const pairHits = [];
    const bigEls = elements.filter((e) => e.rect.width > 2 && e.rect.height > 2);
    for (let i = 0; i < bigEls.length; i++) {
      for (let j = i + 1; j < bigEls.length; j++) {
        const a = bigEls[i], b = bigEls[j];
        if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
        const ra = a.rect, rb = b.rect;
        const intersects = ra.x < rb.x + rb.width && ra.x + ra.width > rb.x && ra.y < rb.y + rb.height && ra.y + ra.height > rb.y;
        if (!intersects) continue;
        pairHits.push({ a: `<${a.tag}>"${a.text}"`, b: `<${b.tag}>"${b.text}"`, rectA: ra, rectB: rb });
      }
    }

    return {
      elements: elements.map(({ el, ...rest }) => rest),
      avatarChecks, pairHits,
    };
  }, platform);

  const pairHits = result.pairHits;

  // ---- 2. 安全区(仅douyin) ----
  const zoneHits = [];
  if (platform === "douyin") {
    for (const e of result.elements) {
      for (const [name, z] of Object.entries(DOUYIN_ZONES)) {
        if (zoneIntersect(e.rect, z)) {
          zoneHits.push({ zone: name, tag: e.tag, text: e.text, rect: e.rect });
        }
      }
    }
  }

  // ---- 3. 最小字号 ----
  const smallFont = result.elements.filter((e) => e.fontSize && e.fontSize < 22 && e.text);

  // ---- 4. 纵向连续空白(粗略法：把所有元素的y区间投影到一条纵轴，找最大空隙，
  //    只在安全区/画布内容区范围内检查) ----
  const yTop = platform === "douyin" ? 240 : 0;
  const yBottom = platform === "douyin" ? 1440 : (vh - 96 - 34); // xhs 减去 footer-band+caveat-row
  const intervals = result.elements
    .filter((e) => e.rect.y + e.rect.height > yTop && e.rect.y < yBottom)
    .map((e) => [Math.max(e.rect.y, yTop), Math.min(e.rect.y + e.rect.height, yBottom)])
    .sort((a, b) => a[0] - b[0]);
  let merged = [];
  for (const iv of intervals) {
    if (merged.length && iv[0] <= merged[merged.length - 1][1] + 1) {
      merged[merged.length - 1][1] = Math.max(merged[merged.length - 1][1], iv[1]);
    } else {
      merged.push([...iv]);
    }
  }
  let maxGap = 0, gapAt = null;
  let cursor = yTop;
  for (const iv of merged) {
    if (iv[0] - cursor > maxGap) { maxGap = iv[0] - cursor; gapAt = [cursor, iv[0]]; }
    cursor = Math.max(cursor, iv[1]);
  }
  if (yBottom - cursor > maxGap) { maxGap = yBottom - cursor; gapAt = [cursor, yBottom]; }

  // ---- 5. 头像放大倍数（源图192px，上限1.5倍=288px） ----
  const AVATAR_SRC = 192, AVATAR_MAX = AVATAR_SRC * 1.5;
  const oversizedAvatars = result.avatarChecks.filter((a) => a.displayW > AVATAR_MAX + 0.5);

  // ---- 6/7. 文本黑名单 + 省略号 ----
  const allText = result.elements.map((e) => e.text).join(" ");
  const blacklistHits = BLACKLIST.filter((w) => allText.includes(w));
  const ellipsisHits = ELLIPSIS.filter((w) => allText.includes(w));

  console.log(`\n[${path.basename(htmlPath)}] (${platform}) ============================`);
  console.log(`  元素总数: ${result.elements.length}`);
  console.log(`  1) 两两boundingBox相交: ${pairHits.length === 0 ? "无" : pairHits.length + "处"}`);
  for (const h of pairHits.slice(0, 20)) console.log(`     ${h.a} x ${h.b}`);
  if (platform === "douyin") {
    console.log(`  2) 与安全区相交: ${zoneHits.length === 0 ? "无" : zoneHits.length + "处"}`);
    for (const h of zoneHits.slice(0, 20)) console.log(`     [${h.zone}] <${h.tag}>"${h.text}" rect=${JSON.stringify(h.rect)}`);
  }
  console.log(`  3) 字号<22px的元素: ${smallFont.length === 0 ? "无" : smallFont.length + "处"}`);
  for (const f of smallFont.slice(0, 20)) console.log(`     <${f.tag}>"${f.text}" fontSize=${f.fontSize}`);
  console.log(`  4) 内容区最大连续纵向空白: ${maxGap.toFixed(0)}px${gapAt ? ` (y=${gapAt[0].toFixed(0)}-${gapAt[1].toFixed(0)})` : ""}  ${maxGap > 80 ? "超过80px！" : "达标(<=80px)"}`);
  console.log(`  5) 头像超过1.5倍上限(${AVATAR_MAX}px): ${oversizedAvatars.length === 0 ? "无" : oversizedAvatars.length + "处"}`);
  for (const a of oversizedAvatars) console.log(`     ${a.src} displayW=${a.displayW.toFixed(1)}px`);
  console.log(`  6) 文本黑名单命中: ${blacklistHits.length === 0 ? "无" : blacklistHits.join(", ")}`);
  console.log(`  7) 省略号命中: ${ellipsisHits.length === 0 ? "无" : ellipsisHits.join(", ")}`);

  const fail = pairHits.length > 0 || zoneHits.length > 0 || smallFont.length > 0 ||
    maxGap > 80 || oversizedAvatars.length > 0 || blacklistHits.length > 0 || ellipsisHits.length > 0;
  await browser.close();
  process.exit(fail ? 1 : 0);
}

main().catch((err) => { console.error(err); process.exit(2); });
