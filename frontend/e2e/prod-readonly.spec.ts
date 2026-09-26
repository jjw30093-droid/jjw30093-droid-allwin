import { test, expect } from "@playwright/test";

/**
 * 只读、可对线上域名运行的匿名 e2e(2026-09-26 从 anonymous.spec.ts 拆出)。
 *
 * 约束:
 * - 不读 data/e2e/seed_info.txt,不依赖种子里的固定比赛 id / 固定数值;
 * - 只断言页面结构与关键文案,数据随时间变化也应该通过(比如概率只断言"三项合计 100",
 *   不断言具体百分比);
 * - 只做 GET 与页面交互,不登录、不写任何数据;
 * - 不含象限图用例(象限图在 feat/quadrant-v2 分支上还会变,相关断言留在 anonymous.spec.ts)。
 *
 * 用法:
 *   线上:  npx playwright test -c <只指向线上域名的配置> prod-readonly.spec.ts
 *   本地:  npx playwright test prod-readonly.spec.ts(走 playwright.config.ts 的种子环境)
 */

const BANNED_COPY = /Bet365|bet365|站长|模型概率|配置还没走完|短信和邮箱还没接/;

async function noHorizontalOverflow(page: import("@playwright/test").Page, width: number) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
}

test("首页匿名可浏览:结构完整,概率条(若有)三项合计恰好 100", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByTestId("mobile-bottom-nav")).toBeVisible();
  await expect(page.getByTestId("theme-toggle")).toBeVisible();
  await expect(page.getByTestId("site-footer")).toBeAttached();
  await noHorizontalOverflow(page, 390);

  // 概率条:不断言具体数值,只断言"每一条的三项加起来恰好 100"(最大余数法取整)
  const labels = await page
    .locator('[aria-label^="胜平负概率"]')
    .evaluateAll((els) => els.map((e) => e.getAttribute("aria-label") ?? ""));
  for (const label of labels) {
    const nums = (label.match(/\d+/g) ?? []).map(Number);
    expect(nums).toHaveLength(3);
    expect(nums[0] + nums[1] + nums[2], label).toBe(100);
  }
});

test("明暗模式可切换并在刷新后保持", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");

  const toggle = page.getByTestId("theme-toggle");
  await expect(toggle).toBeVisible();
  await toggle.click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("allwin-theme")))
    .toBe("dark");

  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.getByTestId("theme-toggle")).toHaveAttribute("aria-pressed", "true");
  await noHorizontalOverflow(page, 390);

  await page.getByTestId("theme-toggle").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});

for (const viewport of [
  { width: 360, height: 800 },
  { width: 430, height: 932 },
  { width: 1280, height: 800 },
]) {
  test(`首页响应式 ${viewport.width}×${viewport.height}:无横向溢出,底部导航只在手机出现`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto("/");
    await noHorizontalOverflow(page, viewport.width);

    const bottomNav = page.getByTestId("mobile-bottom-nav");
    if (viewport.width <= 640) {
      await expect(bottomNav).toBeVisible();
    } else {
      await expect(bottomNav).toBeHidden();
    }
  });
}

test("页脚公众号二维码(桌面)常驻:真实图片解码成功、深浅色都可扫", async ({ page }) => {
  // 手机(<768px)页脚折叠成一行"品牌名 + 关注公众号",二维码在点开的面板里(见下面两条);
  // 桌面继续是常驻大卡片。
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");
  const footer = page.getByTestId("site-footer");
  await expect(footer).toBeVisible();

  const qr = footer.getByRole("img", { name: /公众号二维码/ });
  await expect(qr).toBeVisible();
  // 必须是真的解码出来的图 —— src 404 时 <img> 仍在 DOM 里但 naturalWidth=0,
  // 页面看起来"有二维码"实际是破图,扫不出来。
  await expect
    .poll(() => qr.evaluate((el) => (el as HTMLImageElement).naturalWidth))
    .toBeGreaterThan(0);

  // 深色模式下二维码必须保留白底,否则深色卡面上的黑色码块对比度不足扫不出
  await page.evaluate(() => {
    document.documentElement.dataset.theme = "dark";
  });
  const bg = await qr.evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(bg).toBe("rgb(255, 255, 255)");
});

test("页脚公众号入口(手机):折叠成一行,普通浏览器给名称 + 复制 + 保存二维码", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const footer = page.getByTestId("site-footer");
  await footer.scrollIntoViewIfNeeded();
  // 桌面那张大卡片在手机上不显示
  await expect(footer.getByRole("img", { name: /公众号二维码/ })).toHaveCount(0);
  await footer.getByRole("button", { name: "关注公众号" }).click();
  const dialog = page.getByRole("dialog", { name: "关注公众号" });
  await expect(dialog).toContainText("喵弟数据研究室");
  await expect(dialog.getByRole("button", { name: "复制公众号名称" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "保存二维码到相册" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
});

test.describe("页脚公众号入口(手机,微信内置浏览器 UA)", () => {
  test.use({
    userAgent:
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.44 NetType/WIFI Language/zh_CN",
  });

  test("面板里是真实解码的二维码 + 长按识别提示", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    const footer = page.getByTestId("site-footer");
    await footer.scrollIntoViewIfNeeded();
    await footer.getByRole("button", { name: "关注公众号" }).click();
    const dialog = page.getByRole("dialog", { name: "关注公众号" });
    const qr = dialog.getByRole("img", { name: /公众号二维码/ });
    await expect(qr).toBeVisible();
    await expect
      .poll(() => qr.evaluate((el) => (el as HTMLImageElement).naturalWidth))
      .toBeGreaterThan(0);
    await expect(dialog).toContainText("长按识别二维码关注");
    await expect(dialog.getByRole("button", { name: "复制公众号名称" })).toHaveCount(0);
  });
});

test("联赛速览:四张图真的挂上 ECharts;xG 运气榜可访问且写明口径", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/league/47/overview");
  await expect(page.getByRole("heading", { name: /赛季速览/ })).toBeVisible();
  for (const title of ["主平客分布", "进球时段分布", "大小球命中率", "常见比分"]) {
    await expect(page.getByText(title, { exact: true })).toBeVisible();
  }
  // 图必须真的挂上 ECharts,不是空壳标题
  await expect.poll(() => page.locator("canvas").count(), { timeout: 20_000 }).toBeGreaterThanOrEqual(4);

  await page.goto("/league/47/standings?table_type=xg");
  await expect(page.getByRole("heading", { name: /xG 运气榜/ })).toBeVisible();
  await expect.poll(() => page.locator("canvas").count(), { timeout: 20_000 }).toBeGreaterThan(0);
  // 必须写明是数据源官方 xG 口径,不能被读成"本站模型算的"
  await expect(page.getByText(/不是本站模型输出/)).toBeVisible();
});

test("排名页(手机):首屏同时有 名次/球队/积分/场次/净胜,行高紧凑,切换器互相带着走", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/league/47/standings");
  await expect(page.getByRole("heading", { name: /排名榜/ })).toBeVisible();

  // 可见列的顺序(display:none 的桌面副本不算):名次 球队 积分 场次 净胜 在最前
  const visibleHeads = await page
    .locator("thead th")
    .evaluateAll((ths) =>
      ths.filter((t) => getComputedStyle(t).display !== "none").map((t) => t.textContent?.trim()),
    );
  expect(visibleHeads.slice(0, 5)).toEqual(["名次", "球队", "积分", "场次", "净胜"]);

  const rows = page.locator("tbody tr");
  expect(await rows.count()).toBeGreaterThanOrEqual(10);
  const rowHeight = await rows.first().evaluate((el) => el.getBoundingClientRect().height);
  expect(rowHeight).toBeLessThanOrEqual(40); // 原来 54px,压缩后约 30px

  // 首屏(不滚动)里就能看到积分列的表头,不用左右滑
  const pointsHead = page.locator("thead th").filter({ hasText: "积分" }).first();
  const box = await pointsHead.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x + box!.width).toBeLessThanOrEqual(390);

  // 两个切换器互相带着走:切榜别不丢赛季,切赛季不掉回总榜
  await page.goto("/league/47/standings?season=2024%2F2025&table_type=xg");
  await expect(page.getByTestId("table-type-chip").nth(1)).toHaveAttribute("href", /season=2024/);
  const seasonSelect = page.getByLabel("选择赛季");
  await expect(seasonSelect).toHaveValue("2024/2025");
  await seasonSelect.selectOption("2023/2024");
  await expect(page).toHaveURL(/table_type=xg/);
  await expect(page).toHaveURL(/season=2023/);
});

test("关于我们页面承接平台介绍和合作入口", async ({ page }) => {
  await page.goto("/about");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("关于喵弟");
  await expect(page.getByRole("heading", { name: "合作联系" })).toBeVisible();
});

test("比赛列表渲染真实数据", async ({ page }) => {
  await page.goto("/matches");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("比赛");
  await expect(page.locator(`a[href*="/matches/"]`).first()).toBeVisible();
});

test("历史战绩页与精选页可访问,匿名只有登录引导,没有旧的两段说明", async ({ page }) => {
  await page.goto("/track-record");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("历史战绩");

  await page.goto("/reco");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("每日精选");
  await expect(page.getByText("每天人工精选，开通后在这里查看。")).toBeVisible();
  // 匿名:一个"登录"按钮,带返回路径
  await expect(page.getByRole("link", { name: "登录", exact: true }).first()).toBeVisible();
  // 三个标签都在
  for (const name of ["每日公推", "今日精选", "历史战绩"]) {
    await expect(page.getByRole("link", { name, exact: true })).toBeVisible();
  }
  await expect(page.getByText("每天人工出的推荐")).toHaveCount(0);
});

test("会员与权限页:标题、三步开通指引在最上面,不暴露内部权益键值", async ({ page }) => {
  await page.goto("/pricing");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("会员与权限");
  await expect(page.locator("#how-to-unlock li")).toHaveText([
    "登录账号",
    "通过公众号联系我们开通",
    "回到「精选」页查看",
  ]);
  // 三步指引在套餐卡之前
  const guideY = (await page.locator("#how-to-unlock").boundingBox())!.y;
  const cardY = (await page.getByText("游客").first().boundingBox())!.y;
  expect(guideY).toBeLessThan(cardY);

  await expect(page.getByText("免费账号").first()).toBeVisible();
  await expect(page.getByText("精选授权用户").first()).toBeVisible();
  await expect(page.getByText("Pro", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Premium", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/league:|reco:|odds:|prediction:/)).toHaveCount(0);
});

test("登录页:一句话副标题、账号密码表单、忘记密码提示,没有已删除的说明卡片", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/login");
  // 扫码登录开放(WECHAT_AUTH_ENABLED)时标题会随环境变化(如"扫码登录"),都含"登录"
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(/登录/);
  // 顶栏与内容的留白 16px:/login 的这项检查放在这里,不放进下面多页用例——Cloudflare 对
  // /login 单独有更严的限流(线上实测返回 429 / Error 1015),整份 e2e 里只访问 /login 一次
  expect(await page.evaluate(() => getComputedStyle(document.querySelector("body > main")!).paddingTop)).toBe("16px");
  await expect(page.getByText("登录后可以收藏比赛、查看每日精选。比赛数据不用登录也能看。")).toBeVisible();
  // 账号密码表单:扫码未开放时是常驻主卡片,开放时收在折叠项里——两种形态都要能用
  const username = page.getByLabel("用户名");
  if (!(await username.isVisible().catch(() => false))) {
    await page.getByText("账号密码登录").click();
  }
  await expect(username).toBeVisible();
  await expect(page.getByText("忘记密码？通过公众号联系我们")).toBeVisible();
  await expect(page.getByText("扫码登录还在开通中")).toHaveCount(0);
  await expect(page.getByText("短信和邮箱还没接")).toHaveCount(0);
});

test("面向用户的页面不出现已下线的措辞(Bet365 / 站长 / 模型概率 等)", async ({ page }) => {
  for (const path of ["/", "/matches", "/login", "/pricing", "/about", "/reco", "/leagues"]) {
    await page.goto(path);
    await page.evaluate(() => document.querySelectorAll("details").forEach((d) => (d.open = true)));
    const text = await page.locator("body").innerText();
    expect(text, `${path} 不应含已下线措辞`).not.toMatch(BANNED_COPY);
  }
});


// ─────────────────────────────────────────────────────────────────────────
// 手机端体验第二批(2026-09-26):导航 / 留白 / 折叠 / 控件统一 / 比赛列表
// ─────────────────────────────────────────────────────────────────────────

test("手机底部导航:首页/比赛/联赛/精选/我的;匿名时「我的」→ /login;没有「战绩」", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const nav = page.getByTestId("mobile-bottom-nav");
  await expect(nav).toBeVisible();
  await expect(nav.getByRole("link")).toHaveText(["首页", "比赛", "联赛", "精选", "我的"]);
  await expect(nav.getByRole("link", { name: "联赛" })).toHaveAttribute("href", "/leagues");
  await expect(nav.getByRole("link", { name: "我的" })).toHaveAttribute("href", "/login");
  await expect(nav.getByText("战绩")).toHaveCount(0);
});

test("手机顶部栏:品牌副标题是联赛数量、没有黄色登录按钮、深色模式按钮 ≥44×44 且纯图标", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.locator("header").first().getByText(/^\d+ 个联赛的数据图表$/)).toBeVisible();
  // 顶栏里不再有"登录"入口(手机)
  await expect(page.locator("header").first().getByRole("link", { name: "登录" })).toBeHidden();
  const toggle = page.getByTestId("theme-toggle");
  const box = await toggle.boundingBox();
  expect(box!.width).toBeGreaterThanOrEqual(44);
  expect(box!.height).toBeGreaterThanOrEqual(44);
  // 纯图标:没有边框
  expect(await toggle.evaluate((el) => getComputedStyle(el).borderTopWidth)).toBe("0px");
});

test("桌面主导航:没有「战绩」入口,「每日精选」在;桌面顶栏保留登录入口", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  const top = page.getByRole("navigation", { name: "主导航" });
  await expect(top.getByRole("link", { name: "战绩" })).toHaveCount(0);
  await expect(top.getByRole("link", { name: "每日精选" })).toBeVisible();
  await expect(page.locator("header").first().getByRole("link", { name: "登录" })).toBeVisible();
});

test("旧链接 /track-record 仍可访问,访问时「精选」保持高亮;精选页有三个标签", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/track-record");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("历史战绩");
  await expect(
    page.getByTestId("mobile-bottom-nav").getByRole("link", { name: "精选" }),
  ).toHaveAttribute("aria-current", "page");

  await page.goto("/reco?tab=record");
  const tabs = page.getByRole("navigation", { name: "精选内容切换" });
  await expect(tabs.getByRole("link")).toHaveText(["每日公推", "今日精选", "历史战绩"]);
  await expect(tabs.getByRole("link", { name: "历史战绩" })).toHaveAttribute("aria-current", "page");
});

test("各页面顶部栏与第一块内容之间的留白统一为 16px(手机)", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  // 不含 /login(见上面登录页用例:/login 在线上有更严的限流,只访问一次)
  for (const path of ["/", "/matches", "/leagues", "/league/47/standings", "/reco", "/pricing"]) {
    await page.goto(path);
    const pad = await page.evaluate(() => getComputedStyle(document.querySelector("body > main")!).paddingTop);
    expect(pad, `${path} main padding-top`).toBe("16px");
    // 一条用例里连续打开多个页面,对线上域名会触发 Cloudflare 1015 限流(页面变成
    // 没有 <main> 的错误页)——每次 goto 之间留 1 秒
    await page.waitForTimeout(1000);
  }
});

test("比赛详情(手机):没有旧的两个文字链接;顶栏有返回箭头,无来源时回 /matches", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/matches?status=finished");
  const first = page.locator('a[href^="/matches/"]').first();
  await expect(first).toBeVisible();
  const href = await first.getAttribute("href");
  // 直接打开详情页(没有站内来源):返回箭头应回 /matches
  await page.goto(href!.split("?")[0]);
  await expect(page.getByText("返回当前筛选结果")).toHaveCount(0);
  await expect(page.getByText("查看更多赛果")).toHaveCount(0);
  await expect(page.getByText("查看本周其他比赛")).toHaveCount(0);
  const back = page.getByTestId("header-back");
  await expect(back).toBeVisible();
  const box = await back.boundingBox();
  expect(box!.width).toBeGreaterThanOrEqual(44);
  await back.click();
  await expect(page).toHaveURL(/\/matches(\?|$)/);
});

test("比赛列表(手机):卡片只显示开球时间、没有联赛目录链接、筛选行右侧渐隐、「更多筛选」是带图标的小按钮", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/matches");
  await expect(page.getByText("浏览联赛排名与球队数据")).toHaveCount(0);
  // 时间:卡片中央是 HH:mm(或"时间待定"),不是完整日期
  const times = await page.locator('a[href^="/matches/"] time').allInnerTexts();
  for (const t of times) expect(t).toMatch(/^\d{2}:\d{2}$/);
  // 时间 / 联赛两排筛选:放不下时右侧有渐隐遮罩
  const timeRow = page.getByRole("group", { name: "时间" });
  const leagueRow = page.getByRole("group", { name: "联赛" });
  await expect(timeRow).toBeVisible();
  await expect(leagueRow).toBeVisible();
  await expect(leagueRow.locator("[data-fade-right]")).toHaveAttribute("data-fade-right", "true");
  // 更多筛选:summary 里有 svg 图标,触控区 ≥44px
  const summary = page.locator("summary", { hasText: "更多筛选" });
  await expect(summary.locator("svg")).toHaveCount(1);
  expect((await summary.boundingBox())!.height).toBeGreaterThanOrEqual(44);
});

test("页面级切换统一为 Tabs:联赛二级导航、排名榜别都是横向可滚动的 nav,选中态 aria-current", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/league/47/standings");
  const leagueNav = page.getByRole("navigation", { name: "联赛导航" });
  await expect(leagueNav.getByRole("link")).toHaveText(["速览", "排名", "赛程", "球队数据", "球员榜"]);
  await expect(leagueNav.getByRole("link", { name: "排名" })).toHaveAttribute("aria-current", "page");
  const types = page.getByRole("navigation", { name: "选择榜别" });
  await expect(types.getByRole("link")).toHaveText(["总榜", "主场", "客场", "近期", "xG 榜"]);
  // 选中态不是带下划线的超链接
  const deco = await leagueNav
    .getByRole("link", { name: "排名" })
    .evaluate((el) => getComputedStyle(el).textDecorationLine);
  expect(deco).toBe("none");
});

for (const section of ["team-stats", "players"] as const) {
  test(`${section}:榜单默认前 3 名 + 「查看全部」,顶部有吸顶分组条,点击滚动到对应分组`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/league/47/${section}`);
    const bar = page.getByTestId("board-section-tabs");
    await expect(bar).toBeAttached();
    const tabs = bar.getByRole("tab");
    await expect(tabs.first()).toHaveText("重点数据");
    // 每张榜卡默认只露 3 行:折叠区(第 4–10 名)不可见
    const card = page.locator("details", { hasText: "查看全部" }).first();
    await expect(card).toBeVisible();
    expect(await card.locator("ol").first().locator("li").count()).toBe(3);
    await expect(card.getByText("查看全部")).toBeVisible();
    // 点第二个分组 → 该分组标题滚到吸顶条下面,不被吸顶条/顶栏盖住
    const second = tabs.nth(1);
    const label = (await second.innerText()).trim();
    await second.click();
    // 球队数据页每组有可见 <h2>;球员页分组名由吸顶条给出、不重复标题(整页不增高),
    // 分组本身是带 aria-label 的 region
    const heading =
      section === "players"
        ? page.getByRole("region", { name: label, exact: true })
        : page.getByRole("heading", { name: label, exact: true });
    await expect(heading).toBeVisible();
    await expect
      .poll(async () => {
        const h = (await heading.boundingBox())!.y;
        const barBottom = (await bar.boundingBox())!.y + (await bar.boundingBox())!.height;
        return h >= barBottom - 1;
      })
      .toBe(true);
    // 吸顶条不遮挡底部导航
    const barBox = (await bar.boundingBox())!;
    const navBox = (await page.getByTestId("mobile-bottom-nav").boundingBox())!;
    expect(barBox.y + barBox.height).toBeLessThanOrEqual(navBox.y);
    // 展开:点「查看全部」后最多 10 行
    await card.locator("summary").click();
    expect(await card.locator("li").count()).toBeLessThanOrEqual(10);
  });
}

// ── 手机端体验第三批(2026-09-26):首页首屏 / 比赛详情比分卡 ─────────────

test("首页首屏(手机):第一块是一句话定位 + 三个入口;战绩条不在页面最顶部", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const h1 = page.getByRole("heading", { level: 1 });
  await expect(h1).toHaveText(/^英超、西甲等 \d+ 个联赛的比赛与数据$/);
  const nav = page.getByRole("navigation", { name: "首页入口" });
  await expect(nav.getByRole("link")).toHaveText(["看比赛", "联赛数据", "今日精选"]);
  await expect(nav.getByRole("link", { name: "看比赛" })).toHaveAttribute("href", "/matches");
  await expect(nav.getByRole("link", { name: "联赛数据" })).toHaveAttribute("href", "/leagues");
  await expect(nav.getByRole("link", { name: "今日精选" })).toHaveAttribute("href", "/reco");
  for (const link of await nav.getByRole("link").all()) {
    expect((await link.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  }
  // 定位语在整页最上面;战绩条(若接口有数据)在「今日精选」卡内部,不在它上面
  const heroBox = (await h1.boundingBox())!;
  const strip = page.getByRole("link", { name: "推荐战绩,查看完整记录" });
  if ((await strip.count()) > 0) {
    const stripBox = (await strip.boundingBox())!;
    expect(stripBox.y).toBeGreaterThan(heroBox.y);
    const inPicksCard = await strip.evaluate(
      (el) => !!el.closest("section")?.querySelector("#daily-picks-title"),
    );
    expect(inPicksCard).toBe(true);
    // 中性配色:没有红色边框/粉色背景
    const style = await strip.evaluate((el) => {
      const s = getComputedStyle(el);
      return { border: s.borderTopColor, bg: s.backgroundColor, shadow: s.boxShadow };
    });
    const isRedish = (c: string) => {
      const m = c.match(/\d+(\.\d+)?/g)?.map(Number) ?? [];
      return m.length >= 3 && m[0] > 150 && m[0] - m[1] > 60 && m[0] - m[2] > 60;
    };
    expect(isRedish(style.border), style.border).toBe(false);
    expect(isRedish(style.bg), style.bg).toBe(false);
    expect(style.shadow).toBe("none");
  }
  await noHorizontalOverflow(page, 390);
});

test("首页:今晚/明天/本周始终可点击,分别跳 /matches 对应筛选;今日精选没有「今天还没发」空状态", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const bar = page.getByTestId("match-counts-bar");
  await expect(bar.getByRole("link")).toHaveCount(3);
  await expect(bar.getByRole("link", { name: /^今晚/ })).toHaveAttribute("href", "/matches?window=today");
  await expect(bar.getByRole("link", { name: /^明天/ })).toHaveAttribute("href", "/matches?window=tomorrow");
  await expect(bar.getByRole("link", { name: /^本周/ })).toHaveAttribute("href", "/matches?window=7d");
  await expect(page.getByText("今天还没发")).toHaveCount(0);
  await bar.getByRole("link", { name: /^明天/ }).click();
  await expect(page).toHaveURL(/\/matches\?window=tomorrow/);
});

test("首页停赛期提示:若出现,格式为「国际比赛日,五大联赛 M月D日 恢复」", async ({ page }) => {
  await page.goto("/");
  const note = page.getByTestId("league-break-note");
  if ((await note.count()) > 0) {
    await expect(note).toHaveText(/^国际比赛日，五大联赛 \d{1,2}月\d{1,2}日 恢复$/);
  }
});

test("比赛详情比分卡:队徽外没有灰色方框(与全站队徽一致)", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/matches?status=finished");
  const href = await page.locator('a[href^="/matches/"]').first().getAttribute("href");
  await page.goto(href!.split("?")[0]);
  const frames = page.locator('[class*="crestFrame"]');
  expect(await frames.count()).toBeGreaterThanOrEqual(2);
  for (const el of await frames.all()) {
    const s = await el.evaluate((n) => {
      const c = getComputedStyle(n);
      return { border: c.borderTopWidth, bg: c.backgroundColor, radius: c.borderTopLeftRadius, overflow: c.overflow };
    });
    expect(s.border).toBe("0px");
    expect(s.bg).toBe("rgba(0, 0, 0, 0)");
    expect(s.radius).toBe("0px");
  }
});
