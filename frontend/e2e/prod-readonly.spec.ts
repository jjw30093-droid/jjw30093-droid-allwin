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
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("登录");
  await expect(page.getByText("登录后可以收藏比赛、查看每日精选。比赛数据不用登录也能看。")).toBeVisible();
  await expect(page.getByLabel("用户名")).toBeVisible();
  await expect(page.getByText("忘记密码？通过公众号联系我们")).toBeVisible();
  await expect(page.getByText("扫码登录还在开通中")).toHaveCount(0);
});

test("面向用户的页面不出现已下线的措辞(Bet365 / 站长 / 模型概率 等)", async ({ page }) => {
  for (const path of ["/", "/matches", "/login", "/pricing", "/about", "/reco", "/leagues"]) {
    await page.goto(path);
    await page.evaluate(() => document.querySelectorAll("details").forEach((d) => (d.open = true)));
    const text = await page.locator("body").innerText();
    expect(text, `${path} 不应含已下线措辞`).not.toMatch(BANNED_COPY);
  }
});
