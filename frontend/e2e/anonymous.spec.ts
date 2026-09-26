import { test, expect } from "@playwright/test";
import { API, seedMatchId } from "./helpers";

/**
 * 匿名浏览(**本地种子版**):首页/详情/象限图等依赖 e2e 种子数据的用例。
 * 核心断言:免费层只有最高一项概率(种子 48%),另两项(27%/25%)
 * 不出现在页面,也不出现在匿名 API 响应体里(物理省略,非 CSS 遮挡)。
 *
 * 只能对本地种子环境跑(读 data/e2e/seed_info.txt、断言种子里的固定数值)。
 * 不依赖种子、可对线上域名只读运行的结构与关键文案用例在 prod-readonly.spec.ts
 * (2026-09-26 拆分)。象限图用例仍留在这里:象限图在 feat/quadrant-v2 分支上还会变。
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
  // "我的"(2026-09-26 起,手机端体验第二批):匿名 → /login,已登录 → /account
  // (已登录分支在 e2e/auth.spec.ts 里验证)。此前(2026-08-23)曾恒定指向 /account。
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

test("球队象限图:视角可切换、可分组,缺数据的视角诚实禁用而不是补 0", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/league/47/team-stats");

  await expect(page.getByRole("heading", { name: "球队象限图" })).toBeVisible();
  await expect
    .poll(() => page.locator("canvas").count(), { timeout: 20_000 })
    .toBeGreaterThan(0);

  // 攻防视角的"攻守兼备"必须是真的攻守兼备(预期进球多 + 预期失球少)。
  // 这条断言守的是一个真实修过的 bug:quadrantOf 早先按数值高低命名,
  // 预期失球越低越好的轴上,攻守兼备会被反着标成"对攻型"。
  // (2026-09-09 术语校准把"两头都强"改名为"攻守兼备",这层保护原样保留。)
  const summary = page.getByText(/预期进球 × 预期失球象限图/);
  await expect(summary).toContainText("攻守兼备：");
  for (const team of ["阿森纳", "曼城", "利物浦"]) {
    await expect(summary).toContainText(new RegExp(`攻守兼备：[^；]*${team}`));
  }

  // 2026-09-09 队徽坐标点 + 点击交互:点下方名单里的队名(真按钮)= 点队徽
  const card = page.locator("section", { has: page.getByRole("heading", { name: "球队象限图" }) });
  const panel = card.locator("[aria-live]");
  await expect(panel).toHaveAttribute("data-empty", "true");
  const teamButtons = card.locator("button[aria-pressed]");
  await expect.poll(() => teamButtons.count()).toBeGreaterThanOrEqual(4);
  await teamButtons.nth(0).click();
  await expect(panel).not.toHaveAttribute("data-empty", "true");
  await expect(panel).toContainText(/第 \d+\/\d+/); // 数值带联赛内排名
  await expect(panel).toContainText("场均预期进球 xG");
  // 第二支 → 并排对比;第三支 → FIFO 顶掉最早的,始终最多 2 支
  await teamButtons.nth(1).click();
  await expect(panel).toContainText("差值");
  await expect(card.locator("button[aria-pressed='true']")).toHaveCount(2);
  await teamButtons.nth(2).click();
  await expect(card.locator("button[aria-pressed='true']")).toHaveCount(2);
  await expect(teamButtons.nth(0)).toHaveAttribute("aria-pressed", "false");
  // Esc 清空
  await page.keyboard.press("Escape");
  await expect(panel).toHaveAttribute("data-empty", "true");

  // 攻防/战术/射门质量三个既有视角同属默认分组"攻防总览",一行内直接切换,
  // 不用先点分类按钮
  await page.getByRole("tab", { name: "战术" }).click();
  await expect(page.getByText(/运动战 × 定位球象限图/)).toBeVisible();
  await expect(page.getByText(/多点开花/).first()).toBeVisible();

  // 分组选择器:点另一个类别(控球与推进)= 换到该类别下的视角,
  // 且原类别的视角不再出现在视角行里(证明分组筛选真的生效)
  await page.getByRole("button", { name: "控球与推进" }).click();
  await expect(page.getByRole("tab", { name: "推进方式" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.getByRole("tab", { name: "战术" })).toHaveCount(0);
  await expect(page.getByText(/前场传球占比/).first()).toBeVisible();

  // 切回攻防总览,战术不再被选中(证明类别切换真的换了图,不是原地不动)
  await page.getByRole("button", { name: "攻防总览" }).click();
  await expect(page.getByRole("tab", { name: "攻防" })).toBeVisible();

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
  await expect(page.getByRole("heading", { name: "数据倾向" })).toBeVisible();

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
  // "推荐"不检测——"推荐待发布"是 QuickView 的合法状态文案,不是投注推荐,
  // 检测那个词会和自己的功能打架;真正禁止的措辞是"必胜"/"稳赚"/"红单"。
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
  // 种子未发布任何推荐单 → 如实"推荐待发布",不得伪造已发布。
  // 2026-08-14 重设计:内容分 tab(看点/数据/赔率)后,"查看详细数据↓"
  // 锚点已删除——不再有"往下滚"这件事,断言随之移除。
  const quick = page.getByTestId("quick-view");
  await expect(quick).toBeVisible();
  await expect(quick.getByText("推荐待发布")).toBeVisible();
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

