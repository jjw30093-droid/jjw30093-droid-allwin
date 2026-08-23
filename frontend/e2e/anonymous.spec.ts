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
  // 模型概率面板已下架(模型未经真实训练前不展示任何概率);硬编码的"本场
  // 依据"/"近期战绩"面板同批撤掉,首屏改由 Bet365 1x2 赔率折算的胜平负
  // 概率条承载(E2E 种子固定灌 1.50/4.60/7.50 → 66%/21%/13%,见
  // tests/e2e/seed_e2e.py::_seed_win_probability_odds)。
  await expect(featured.getByText("主胜")).toBeVisible();
  await expect(featured.getByText("66%")).toBeVisible();
  await expect(featured.getByText("21%")).toBeVisible();
  await expect(featured.getByText("13%")).toBeVisible();
  await expect(featured.getByText("公开结论")).toHaveCount(0);
  await expect(featured.getByRole("link", { name: /查看.+完整分析/ })).toBeVisible();
  // 队徽必须真正渲染出图片,不能退化成首字母兜底(next.config.ts 的
  // /api/v1/media 同源 rewrite + 队徽已同步,见 docs/data-plan.md)。
  // 断言三层:①没有兜底元素;②是版本化同源地址;③图片真的解码了
  // (naturalWidth>0——地址 404 时 TeamBadge 的 onError 会换成兜底,
  // 任一层出问题都会挂)。
  // 注意:SSR 输出的 src 是相对路径,但 next/image 只要收到 onError prop,
  // hydration 后就会执行 `img.src = img.src`(node_modules/next/dist/client/
  // image-component.js,为了补发 hydration 前丢失的 error 事件),把属性改写成
  // 解析后的绝对地址——所以这里解析 URL 后分别断言 origin 与页面同源(比只查
  // 相对路径更严:能拒绝任何第三方 CDN 外链)、pathname 与版本参数格式。
  await expect(featured.getByTestId("team-badge-fallback")).toHaveCount(0);
  const featuredCrests = featured.locator('[data-testid="team-badge-image"] img');
  await expect(featuredCrests).toHaveCount(2);
  const crestSrc = await featuredCrests.first().getAttribute("src");
  expect(crestSrc).not.toBeNull();
  const crestUrl = new URL(crestSrc!, page.url());
  expect(crestUrl.origin).toBe(new URL(page.url()).origin);
  expect(crestUrl.pathname).toMatch(/^\/api\/v1\/media\/team-crests\/fotmob\/\d+\.png$/);
  expect(crestUrl.search).toMatch(/^\?v=[0-9a-f]{12}$/);
  await expect
    .poll(() =>
      featuredCrests.evaluateAll((imgs) =>
        imgs.every((img) => (img as HTMLImageElement).naturalWidth > 0),
      ),
    )
    .toBe(true);

  // 首屏结构(信息架构改版):今晚/明天/未来7天计数条在最上,重点卡紧随;
  // 计数条与重点卡的"公开结论"必须都落在第一屏内(整卡允许略超出折叠线)。
  const countsBar = page.getByTestId("match-counts-bar");
  await expect(countsBar).toBeVisible();
  const countsBox = await countsBar.boundingBox();
  expect(countsBox).not.toBeNull();
  expect(countsBox?.y ?? Infinity).toBeLessThanOrEqual(300);
  const probBox = await featured.getByText("主胜").boundingBox();
  expect(probBox).not.toBeNull();
  expect(
    (probBox?.y ?? Infinity) + (probBox?.height ?? 0),
  ).toBeLessThanOrEqual(844);

  // 概率条底部必须标注赔率观测时间(§6.2 不伪装:折算概率不是实时数据)
  await expect(featured.getByText("采集于")).toBeVisible();

  // 今日更新状态:2026-08-23 起首屏只常驻一行"赛程 · 赔率"摘要(赛程/赔率
  // 两条时间戳合并展示),"推荐更新"不再放进这个区块——推荐是站长自己的
  // 发布节奏,不是数据新鲜度指标。赛程/赔率任一进入 STALE/UNAVAILABLE 才
  // 展开成逐行,种子数据两者皆正常,这里断言的是折叠态的常驻摘要。
  const freshness = page.getByTestId("freshness-line");
  await expect(freshness).toBeVisible();
  await expect(freshness).toContainText("赛程");
  await expect(freshness).toContainText("赔率");
  await expect(freshness).not.toContainText("推荐更新");

  // 本周比赛列表(替代旧的横滑"其他比赛"+"近期赛程"重复区):不含重点场
  const weekList = page.getByTestId("this-week-matches");
  await expect(weekList).toBeVisible();
  await expect(weekList.locator(`a[href*="/matches/${seedMatchId()}"]`)).toHaveCount(0);

  // 今日精选模块:2026-08-23 起与"近30天推荐记录"合并成一张卡,只保留一个
  // 主 CTA,不再各自渲染标题——「近N天推荐记录」这个独立标题已被合并掉。
  await expect(page.getByRole("heading", { name: "今日精选" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /近\d+天推荐记录/ }),
  ).toHaveCount(0);

  const bottomNav = page.getByTestId("mobile-bottom-nav");
  await expect(bottomNav).toBeVisible();
  // "我的"2026-08-23 起恒定指向 /account(不论登录状态),由 /account 自己
  // 处理匿名态展示(已验证该页面有 anonymous 分支,不是裸跳转/空白页),
  // 不再是 authed ? "/account" : "/login" 这种在导航层判断认证状态的写法。
  await expect(bottomNav.getByRole("link", { name: "我的" })).toHaveAttribute(
    "href",
    "/account",
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
    await expect(featured.getByText("主胜")).toBeVisible();
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

test("页脚公众号二维码常驻:真实图片解码成功、深浅色都可扫", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const footer = page.getByTestId("site-footer");
  await expect(footer).toBeVisible();

  // 私域转化是站长商业链路的最后一跳(短视频 → 网站 → 公众号 → 私域),
  // 此前全站没有页脚、也没有任何常驻的关注入口。
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

test("联赛速览四图 + xG 运气榜:此前零消费的银层/xg 档真的渲染出来", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });

  // ① 速览 tab(联赛页新默认落点):四张图消费的四张银层表此前前端零消费,
  //    三个公开联赛页 EChart import 数是 0。
  await page.goto("/league/47/overview");
  await expect(page.getByRole("heading", { name: /赛季速览/ })).toBeVisible();
  for (const title of ["主平客分布", "进球时段分布", "大小球命中率", "常见比分"]) {
    await expect(page.getByText(title, { exact: true })).toBeVisible();
  }
  // 图必须真的挂上 ECharts,不是空壳标题
  await expect
    .poll(() => page.locator("canvas").count(), { timeout: 20_000 })
    .toBeGreaterThanOrEqual(4);

  // ② xG 运气榜:fact_league_table 的 xg 档(695 行)此前 100% 不可见
  //    (standings 硬编码 table_type='all')。
  await page.goto("/league/47/standings?table_type=xg");
  await expect(page.getByRole("heading", { name: /xG 运气榜/ })).toBeVisible();
  await expect
    .poll(() => page.locator("canvas").count(), { timeout: 20_000 })
    .toBeGreaterThan(0);
  // 必须写明是数据源官方 xG 口径,不能被读成"本站模型算的"
  await expect(page.getByText(/不是本站模型输出/)).toBeVisible();

  // ③ 两个切换器互相带着走:切榜别不丢赛季,切赛季不掉回总榜
  await page.goto("/league/47/standings?season=2024%2F2025&table_type=xg");
  const seasonChip = page.getByTestId("season-switcher-chip").first();
  await expect(seasonChip).toHaveAttribute("href", /table_type=xg/);
  const homeChip = page.getByTestId("table-type-chip").nth(1);
  await expect(homeChip).toHaveAttribute("href", /season=2024/);
});

test("球队象限图:三视角可切换,缺数据的视角诚实禁用而不是补 0", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/league/47/team-stats");

  await expect(page.getByRole("heading", { name: "球队象限图" })).toBeVisible();
  await expect
    .poll(() => page.locator("canvas").count(), { timeout: 20_000 })
    .toBeGreaterThan(0);

  // 攻防视角的"两头都强"必须是真的攻守兼备(创造多 + 被创造少)。
  // 这条断言守的是一个真实修过的 bug:quadrantOf 早先按数值高低命名,
  // 被创造 xG 越低越好的轴上,攻守兼备会被反着标成"对攻型"。
  const summary = page.getByText(/进攻创造 × 防守让出象限图/);
  await expect(summary).toContainText("两头都强：");
  for (const team of ["阿森纳", "曼城", "利物浦"]) {
    await expect(summary).toContainText(new RegExp(`两头都强：[^；]*${team}`));
  }

  // 三个视角都在,点了要真的换图
  await page.getByRole("tab", { name: "战术" }).click();
  await expect(page.getByText(/运动战 × 定位球象限图/)).toBeVisible();
  await expect(page.getByText(/两条路都通/).first()).toBeVisible();

  // 数据源没给 xg 档的赛季:攻防视角禁用并说明原因,默认落到战术,不静默补 0
  await page.goto("/league/53/team-stats?season=2020%2F2021");
  const gated = page.getByRole("tab", { name: "攻防" });
  await expect(gated).toBeDisabled();
  await expect(gated).toHaveAttribute("title", /缺少此视角所需的数据/);
  await expect(page.getByRole("tab", { name: "战术" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
});

test("赛前市场卡:数据倾向 + 折叠归因(赛前之墙唯一的比赛特有内容)", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const id = seedMatchId();

  await page.goto(`/matches/${id}`);
  // 板块标题 2026-08-23 从"数据倾向"改名"盘口参考"(内部类目名换成竞彩
  // 用户实际会用的词);MarketCard 内部倾向格下方的小字 caption 仍叫
  // "数据倾向",与这里的区块大标题是两处不同文本,互不影响。
  await expect(page.getByRole("heading", { name: "盘口参考" })).toBeVisible();

  // 两张卡都要出现(2026-08-20 站长要求「数据倾向」板块只保留罚牌/角球,
  // 大小球不展示——backend/queries/market_cards.py::match_market_cards
  // 过滤掉了 goals),标签与盘口线是硬编码的市场定义,不随数据变化。
  await expect(page.getByRole("heading", { name: "大小球" })).toHaveCount(0);
  for (const [label, line] of [
    ["罚牌", "3.5"],
    ["角球", "9.5"],
  ] as const) {
    await expect(page.getByRole("heading", { name: label })).toBeVisible();
    await expect(page.getByText(`盘口线 ${line}`)).toBeVisible();
  }

  // 罚牌在种子数据上是有效信号(★★),必须渲染倾向结论(2026-08-14
  // 重设计:结论区是"偏大/偏小"大字 + "数据倾向"小字说明两个独立元素,
  // 不再是"数据倾向:偏大"一句话),且明确不是投注建议措辞。
  //
  // 具体是"偏大"还是"偏小"不做硬编码断言:hit_rate 来自
  // backend.eval.calibrate_markets 对核心库真实历史比赛的滚动回测(见该
  // 模块文档字符串的方法论),种子脚本每次运行都会对着当时的真实累积数据
  // 重新标定——这是故意的(CLAUDE.md 禁止编造模型表现/伪造回测样本),
  // 不是"种子数据该固定却没固定"的 bug,所以罚牌这张卡具体落在偏大还是
  // 偏小,会随生产数据量增长真实漂移,而且两个方向都不违反"有信号"这条
  // 断言的本意。2026-08-21 实测已从最初断言的"偏大"变成"偏小",证实了
  // 这个漂移是真实发生的,而不是假设。角球卡在种子数据上不单调、没有
  // 方向(见下方断言);只要页面上"偏大"或"偏小"任一恰好出现一次,就说明
  // 罚牌渲染了真实倾向且没有其它市场卡串入多余的方向文案。
  const overCount = await page.getByText("偏大").count();
  const underCount = await page.getByText("偏小").count();
  expect(overCount + underCount).toBe(1);
  await expect(page.getByText("数据倾向").first()).toBeVisible();
  // "推荐"本身不检测——每日精选相关的合法文案(如"每日精选需要单独授权")
  // 会用到这个词,不是投注推荐,检测那个词会和自己的功能打架;真正禁止的
  // 措辞是"必胜"/"稳赚"/"红单"。
  await expect(page.getByText("必胜")).toHaveCount(0);
  await expect(page.getByText("稳赚")).toHaveCount(0);
  await expect(page.getByText("红单")).toHaveCount(0);

  // 角球在种子数据上外样本不单调(2026-08-12 实测),必须诚实降级为
  // "不够稳定",不能编一个方向出来——这是本功能最容易在未来被破坏的地方
  // (标定重跑后如果这条断言开始失败,说明角球线真的变得单调了,应该
  // 更新断言而不是删掉它)。
  await expect(page.getByText(/样本外测试中不够稳定/)).toBeVisible();

  // 折叠区默认视觉隐藏(原生 <details>,内容仍在 DOM 里但不可见),
  // 展开后驱动因子明细才真的可见。
  const foulsRow = page.getByText("犯规").first();
  await expect(foulsRow).toBeHidden();
  const firstDetails = page.locator("details").first();
  await firstDetails.locator("summary").click();
  await expect(foulsRow).toBeVisible();
});

test("关于我们页面承接平台介绍和合作入口", async ({ page }) => {
  await page.goto("/about");
  await expect(page.getByRole("heading", { name: "关于我们" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "合作联系" })).toBeVisible();
});

test("比赛列表渲染真实数据", async ({ page }) => {
  await page.goto("/matches");
  await expect(page.locator(`a[href*="/matches/"]`).first()).toBeVisible();
});

test("详情页免费概率投影(API 层)+ 本场看点不渲染任何概率(UI 层)", async ({
  page,
  request,
}) => {
  const id = seedMatchId();

  // API 层(2026-08-16 权限口径修正):PredictionDTO 恒为单一完整形状
  // (home_probability/draw_probability/away_probability 恒为必填数字),
  // 不再有 free/full 两套 DTO,也不再有 top_probability 这个字段名。
  // 模型未经真实训练前详情页 UI 不渲染概率(用户拍板,见比赛详情页重设计
  // 计划 §四②),但后端端点契约本身不降级。
  const res = await request.get(`${API}/api/v1/matches/${id}/prediction`);
  expect(res.ok()).toBeTruthy();
  const predictionBody = await res.json();
  expect(JSON.stringify(predictionBody)).not.toContain("top_probability");
  if (predictionBody.available) {
    expect(typeof predictionBody.prediction.home_probability).toBe("number");
    expect(typeof predictionBody.prediction.draw_probability).toBe("number");
    expect(typeof predictionBody.prediction.away_probability).toBe("number");
  }

  // UI 层:详情页完全不渲染概率数字(种子固定灌 48%/27%/25%,任一出现
  // 都说明概率 UI 未被摘除干净)。
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
  await expect(page.getByText("48%")).toHaveCount(0);
  await expect(page.getByText("27%")).toHaveCount(0);
  await expect(page.getByText("25%")).toHaveCount(0);

  // 本场看点(先结论后证据):推荐存在性状态。
  // 2026-08-23 改版:没有已发布精选时,"推荐待发布"这种内部发布流程状态
  // 不再展示给用户(对用户没有信息量)——整个 quick-view 区块直接不渲染,
  // 不是伪造已发布,也不是空话占位。种子未发布任何推荐单,所以这里断言
  // 整个区块缺席。
  // 2026-08-14 重设计:内容分 tab(看点/数据/赔率)后,"查看详细数据↓"
  // 锚点已删除——不再有"往下滚"这件事,断言随之移除。
  await expect(page.getByTestId("quick-view")).toHaveCount(0);
  await expect(page.getByText("推荐待发布")).toHaveCount(0);
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

  await page.goto("/reco");
  // 每日精选:匿名只有登录引导(无任何战绩数字),登录按钮带返回路径
  await expect(
    page.getByText("登录后可免费查看全部历史战绩").first(),
  ).toBeVisible();
  const recoLogin = page.getByRole("link", { name: "免费登录查看战绩" });
  await expect(recoLogin).toHaveAttribute("href", "/login?next=/reco");
  await expect(page.getByRole("link", { name: "先看公开比赛资料" })).toBeVisible();

  await page.goto("/pricing");
  // 三层权限说明:游客/注册用户/精选授权;不得再出现付费套餐时代的
  // Pro/Premium 残留文案,也不得暴露内部 entitlement 键值。
  await expect(page.getByRole("heading", { name: "访问权限说明" })).toBeVisible();
  await expect(page.getByText("注册用户").first()).toBeVisible();
  await expect(page.getByText("精选授权用户").first()).toBeVisible();
  await expect(page.getByText("Pro", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Premium", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/league:|reco:|odds:|prediction:/)).toHaveCount(0);
});
