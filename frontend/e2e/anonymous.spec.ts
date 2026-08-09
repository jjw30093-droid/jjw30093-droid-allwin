import { test, expect } from "@playwright/test";
import { API, seedMatchId } from "./helpers";

/**
 * 匿名浏览:首页/比赛列表/详情/信任页。
 * 核心断言:免费层只有最高一项概率(种子 48%),另两项(27%/25%)
 * 不出现在页面,也不出现在匿名 API 响应体里(物理省略,非 CSS 遮挡)。
 */

test("首页匿名可浏览", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const featured = page.getByTestId("featured-match-card");
  await expect(featured).toBeVisible();
  await expect(featured.getByText("公开结论")).toBeVisible();
  await expect(featured.getByText("48%")).toBeVisible();
  await expect(featured.getByText("本场依据")).toBeVisible();
  await expect(featured.getByRole("link", { name: /查看.+完整分析/ })).toBeVisible();
  // 队徽必须真正渲染出图片,不能退化成首字母兜底(next.config.ts 的
  // /api/v1/media 同源 rewrite + 五大联赛队徽已同步,见 docs/data-plan.md)。
  // 断言三层:①没有兜底元素;②是版本化同源地址;③图片真的解码了
  // (naturalWidth>0——地址 404 时 TeamBadge 的 onError 会换成兜底,
  // 任一层出问题都会挂)。
  await expect(featured.getByTestId("team-badge-fallback")).toHaveCount(0);
  const featuredCrests = featured.locator('[data-testid="team-badge-image"] img');
  await expect(featuredCrests).toHaveCount(2);
  await expect(featuredCrests.first()).toHaveAttribute(
    "src",
    /^\/api\/v1\/media\/team-crests\/fotmob\/\d+\.png\?v=[0-9a-f]{12}$/,
  );
  await expect
    .poll(() =>
      featuredCrests.evaluateAll((imgs) =>
        imgs.every((img) => (img as HTMLImageElement).naturalWidth > 0),
      ),
    )
    .toBe(true);

  const featuredBox = await featured.boundingBox();
  expect(featuredBox).not.toBeNull();
  expect((featuredBox?.y ?? 0) + (featuredBox?.height ?? 0)).toBeLessThanOrEqual(844);

  const ticker = page.getByTestId("secondary-match-ticker");
  await expect(ticker).toBeVisible();
  await expect(ticker.locator(`a[href="/matches/${seedMatchId()}"]`)).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "公开战绩" })).toBeVisible();
  await expect(page.getByText(/^(公开验证中|连续公开|暂不可用)$/)).toBeVisible();

  const bottomNav = page.getByTestId("mobile-bottom-nav");
  await expect(bottomNav).toBeVisible();
  await expect(bottomNav.getByRole("link", { name: "我的" })).toHaveAttribute(
    "href",
    "/login",
  );

  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await expect(page.getByText("27%")).toHaveCount(0);
  await expect(page.getByText("25%")).toHaveCount(0);
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
  await expect(page.getByTestId("theme-toggle")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);

  await page.getByTestId("theme-toggle").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});

for (const viewport of [
  { width: 360, height: 800 },
  { width: 430, height: 932 },
  { width: 1280, height: 800 },
]) {
  test(`首页响应式 ${viewport.width}×${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto("/");

    const featured = page.getByTestId("featured-match-card");
    await expect(featured.getByText("公开结论")).toBeVisible();
    await expect(featured.getByText("本场依据")).toBeVisible();
    await expect(featured.getByRole("link", { name: /查看.+完整分析/ })).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(viewport.width);

    const bottomNav = page.getByTestId("mobile-bottom-nav");
    if (viewport.width <= 640) {
      await expect(bottomNav).toBeVisible();
    } else {
      await expect(bottomNav).toBeHidden();
    }
  });
}

test("关于我们页面承接平台介绍和合作入口", async ({ page }) => {
  await page.goto("/about");
  await expect(page.getByRole("heading", { name: "关于我们" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "合作联系" })).toBeVisible();
});

test("比赛列表渲染真实数据", async ({ page }) => {
  await page.goto("/matches");
  await expect(page.locator(`a[href*="/matches/"]`).first()).toBeVisible();
});

test("详情页免费概率:只出现最高一项", async ({ page, request }) => {
  const id = seedMatchId();

  // API 层:匿名响应体不含受限字段
  const res = await request.get(`${API}/api/v1/matches/${id}/prediction`);
  expect(res.ok()).toBeTruthy();
  const body = await res.text();
  expect(body).toContain("top_probability");
  expect(body).not.toContain("draw_probability");
  expect(body).not.toContain("away_probability");

  // UI 层:48% 可见,27%/25% 不存在
  await page.goto(`/matches/${id}`);
  await expect(page.getByTestId("team-badge-fallback")).toHaveCount(0);
  const detailCrests = page.locator('[data-testid="team-badge-image"] img');
  await expect(detailCrests).toHaveCount(2);
  await expect
    .poll(() =>
      detailCrests.evaluateAll((imgs) =>
        imgs.every((img) => (img as HTMLImageElement).naturalWidth > 0),
      ),
    )
    .toBe(true);
  await expect(page.getByText("48%").first()).toBeVisible();
  await expect(page.getByText("27%")).toHaveCount(0);
  await expect(page.getByText("25%")).toHaveCount(0);
});

test("队徽走同源媒体路由:Web 源必须与 API 源返回同一张 PNG", async ({ request }) => {
  // 后端下发的是相对地址(schemas.py TeamRef.crest_url),浏览器按 Web 源
  // 解析——这条直接验证 next.config.ts 的同源 rewrite 本身工作正常,不依赖
  // 页面渲染细节,是本轮改动最直接的回归锁定。
  const detail = await request.get(`${API}/api/v1/matches/${seedMatchId()}`);
  expect(detail.ok()).toBeTruthy();
  const crestPath: string | null = (await detail.json()).match.home.crest_url;
  expect(crestPath, "种子比赛主队缺少队徽,先跑 backend.cli.sync_team_crests").not.toBeNull();
  expect(crestPath!).toMatch(/^\/api\/v1\/media\/team-crests\/fotmob\/\d+\.png\?v=[0-9a-f]{12}$/);

  // baseURL 是 Web 源(playwright.config.ts 的 use.baseURL);相对路径请求
  // 会打到 :3010,证明"没有 Nginx 时也同源可达"。
  const viaWeb = await request.get(crestPath!);
  expect(viaWeb.status()).toBe(200);
  expect(viaWeb.headers()["content-type"]).toContain("image/png");
  const viaApi = await request.get(`${API}${crestPath}`);
  expect((await viaWeb.body()).equals(await viaApi.body())).toBe(true);
});

test("公开战绩/模型说明/定价页可访问且诚实", async ({ page }) => {
  await page.goto("/track-record");
  await expect(page.getByText(/正式|口径/).first()).toBeVisible();

  await page.goto("/about-model");
  await expect(page.getByText(/Dixon|校准|RPS/).first()).toBeVisible();

  await page.goto("/pricing");
  await expect(page.getByText("Pro").first()).toBeVisible();
});
