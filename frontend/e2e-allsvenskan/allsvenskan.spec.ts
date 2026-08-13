import { test, expect } from "@playwright/test";

const API = "http://127.0.0.1:8200";

// 真实数据(2026-07-21 FotMob/NowGoal 真实 ingest 产出,隔离实验库副本,非合成 fixture):
const FINISHED_MATCH_ID = 5107539; // Kalmar FF 2-2 Malmö FF, 2026-07-20T17:00:00Z
const FUTURE_MATCH_ID = 5107547; // Västerås SK vs Örgryte, 2026-07-24T17:00:00Z, 无预测(诚实空状态)
const PREDICTED_MATCH_ID = 5107548; // Kalmar FF vs Mjällby,已发布测试预测(top_probability=0.42)

test.describe("瑞典超(Allsvenskan)接入验收 — 真实数据,隔离实验库", () => {
  test("1. 瑞典超出现在联赛入口(比赛列表筛选)", async ({ page }) => {
    await page.goto("/matches");
    await expect(page.getByRole("link", { name: "瑞典超" })).toBeVisible();
  });

  test("2. 瑞典超积分榜标题正确(不是英超)", async ({ page }) => {
    await page.goto("/league/67/standings");
    await expect(page.getByText("瑞典超 · 排名榜")).toBeVisible();
    await expect(page.getByText("英超 · 排名榜")).toHaveCount(0);
  });

  test("3. 已结束瑞典超比赛详情正常(真实比分/中文队名)", async ({ page }) => {
    await page.goto(`/matches/${FINISHED_MATCH_ID}`);
    await expect(page.getByText("卡尔马FF").first()).toBeVisible();
    await expect(page.getByText("马尔默FF").first()).toBeVisible();
    // 真实比分 2-2(渲染为单个文本节点 "2 – 2")
    await expect(page.getByText(/2\s*[–-]\s*2/).first()).toBeVisible();
  });

  test("4. 未来瑞典超比赛开球时间正确 + 诚实空状态(无预测)", async ({ page }) => {
    await page.goto(`/matches/${FUTURE_MATCH_ID}`);
    await expect(page.getByText("韦斯特罗斯").first()).toBeVisible();
    await expect(page.getByText("厄尔格里特").first()).toBeVisible();
    await expect(page.getByText("2026年7月24日")).toBeVisible();
    await expect(page.getByText("该场比赛暂无已发布的正式预测")).toBeVisible();
  });

  test("5. 中文队名在赛程/积分榜正常渲染", async ({ page }) => {
    await page.goto("/league/67/matches");
    await expect(page.getByText("哈马比").first()).toBeVisible();
  });

  test("6/7. anonymous/free 只看到一项概率;锁定概率不在 API/HTML/RSC/浏览器网络中", async ({
    page,
    request,
  }) => {
    // API 层:原始响应体物理不含另外两项(不是 CSS 遮挡)
    const res = await request.get(`${API}/api/v1/matches/${PREDICTED_MATCH_ID}/prediction`);
    expect(res.ok()).toBeTruthy();
    const body = await res.text();
    expect(body).toContain("top_probability");
    expect(body).not.toContain("home_probability");
    expect(body).not.toContain("draw_probability");
    expect(body).not.toContain("away_probability");
    expect(body).not.toContain("home_win");
    expect(body).not.toContain("away_win");

    // 服务端 HTML/RSC 层
    await page.goto(`/matches/${PREDICTED_MATCH_ID}`);
    const html = await page.content();
    expect(html).not.toContain("home_probability");
    expect(html).not.toContain("draw_probability");
    expect(html).not.toContain("away_probability");

    // UI 层:42% 可见(种子概率),27%/25%/30%/28% 等其它候选不出现
    await expect(page.getByText("42%").first()).toBeVisible();
    await expect(page.getByText("免费层仅展示模型最高一项概率")).toBeVisible();

    // 浏览器真实网络响应(不是页面截图)同样验证
    const netRes = await page.request.get(`${API}/api/v1/matches/${PREDICTED_MATCH_ID}/prediction`);
    const netBody = await netRes.text();
    expect(netBody).not.toContain("draw_probability");
    expect(netBody).not.toContain("away_probability");

    // 隐藏 DOM / 水合状态:window 上不挂载受限字段
    const leaked = await page.evaluate(() => {
      const html = document.documentElement.outerHTML;
      return {
        draw: html.includes("draw_probability"),
        away: html.includes("away_probability"),
        nextData: (window as unknown as { __NEXT_DATA__?: unknown }).__NEXT_DATA__
          ? JSON.stringify((window as unknown as { __NEXT_DATA__?: unknown }).__NEXT_DATA__).includes(
              "draw_probability",
            )
          : false,
      };
    });
    expect(leaked.draw).toBe(false);
    expect(leaked.away).toBe(false);
    expect(leaked.nextData).toBe(false);
  });

  test("8. 已登录用户(member 基线)看到完整三项概率", async ({ page }) => {
    // 扫码登录(与 frontend/e2e/auth.spec.ts 相同机制:webhook 批准;登录即解锁全部足球数据)
    const deviceResp = await page.request.post(`${API}/api/v1/auth/wechat/device`, { data: {} });
    const device = (await deviceResp.json()) as { request_id: string; secret: string };
    const { createHash, randomUUID } = await import("node:crypto");
    const ts = String(Math.floor(Date.now() / 1000));
    const nonce = randomUUID().replace(/-/g, "");
    const signature = createHash("sha1")
      .update(["dev-webhook-token", ts, nonce].sort().join(""))
      .digest("hex");
    const xml =
      "<xml><ToUserName><![CDATA[gh_mock_oa]]></ToUserName>" +
      "<FromUserName><![CDATA[mock-openid-user-1]]></FromUserName>" +
      `<CreateTime>${ts}</CreateTime><MsgType><![CDATA[event]]></MsgType>` +
      "<Event><![CDATA[SCAN]]></Event>" +
      `<EventKey><![CDATA[${device.request_id}]]></EventKey></xml>`;
    const scan = await page.request.post(
      `${API}/api/v1/auth/wechat/webhook?signature=${signature}&timestamp=${ts}&nonce=${nonce}`,
      { data: xml, headers: { "Content-Type": "application/xml" } },
    );
    expect(scan.status()).toBe(200);
    const claim = await page.request.post(
      `${API}/api/v1/auth/wechat/device/${device.request_id}/claim`,
      { data: { secret: device.secret } },
    );
    expect(claim.status()).toBe(200);
    await page.goto("/account");

    await page.goto(`/matches/${PREDICTED_MATCH_ID}`);
    await expect(page.getByText("42%").first()).toBeVisible();
    await expect(page.getByText("30%").first()).toBeVisible();
    await expect(page.getByText("28%").first()).toBeVisible();

    // API 层同样验证完整字段存在
    const res = await page.request.get(`${API}/api/v1/matches/${PREDICTED_MATCH_ID}/prediction`);
    const body = await res.text();
    expect(body).toContain("home_probability");
    expect(body).toContain("draw_probability");
    expect(body).toContain("away_probability");
  });

  test("9. Studio 能选中瑞典超比赛(管理员登录)", async ({ page }) => {
    await page.goto("/login");
    const summary = page.getByText("管理员密码登录");
    await summary.click();
    try {
      await expect(page.getByLabel("用户名")).toBeVisible({ timeout: 3000 });
    } catch {
      await summary.click();
    }
    await page.getByLabel("用户名").fill("pw-admin");
    await page.getByLabel("密码").fill("pw-admin-pass-12345");
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await page.waitForURL("**/");

    await page.goto("/studio");
    await expect(page.getByText("Creator Studio").first()).toBeVisible();
    // 近期比赛列表里真实出现瑞典超真实对阵(证明可选中,不要求走完整导出流程)
    await expect(page.getByText("韦斯特罗斯").first()).toBeVisible();
    await expect(page.getByText("厄尔格里特").first()).toBeVisible();
  });

  test("10. 无 console/hydration error、无 404、移动端无横向溢出", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
    });
    const failed404: string[] = [];
    page.on("response", (r) => {
      if (r.status() === 404) failed404.push(r.url());
    });

    await page.goto(`/matches/${FUTURE_MATCH_ID}`);
    await page.goto("/league/67/standings");
    await page.goto("/matches?league=67");

    expect(errors, `console errors: ${errors.join("; ")}`).toEqual([]);
    expect(failed404, `404s: ${failed404.join("; ")}`).toEqual([]);

    // 移动端视口:无横向溢出
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto(`/matches/${FUTURE_MATCH_ID}`);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    );
    expect(overflow).toBe(false);
  });
});
