/**
 * /admin 「每日精选」RecoTab 完整工作流(P4.B,2026-08-16)。
 *
 * 上一阶段(P4.A)已经把后端能力建好(筛选/分页、发布溯源校验、待确认标记、
 * 结算来源、会员预览端点);这一阶段是纯消费 + UI 搭建。这里只验证前端行为,
 * 后端契约由 tests/backend/test_reco.py 等既有测试覆盖。
 *
 * 覆盖点(任务书要求的最小集合):
 * - 筛选参数变化时正确带上新的查询请求;
 * - 发布按钮点击后不立即触发网络请求,必须先经过站内二次确认面板;
 * - 编辑表单提交调用的是既有 PATCH /admin/reco/slips/{id},不是新端点;
 * - 待确认标记(needs_review)只在 needs_review=true 时渲染;
 * - 预览面板正确调用 GET /admin/reco/slips/{id}/preview,并把会员可见字段
 *   与仅后台可见字段区分展示。
 *
 * 2026-08-19:发布二次确认从 window.confirm 换成站内内联面板(与既有
 * 结算/作废面板同一套交互约定)——真实用户报告在手机上点"发布"没反应,
 * 排查发现是原生 confirm() 弹窗被忽略/划掉,请求根本没发出去,且没有任何
 * 页面反馈。原生弹窗依赖浏览器/系统行为,不同环境表现不一致;站内面板是
 * 站点自己完全可控的 UI,点"确认发布"才真正发请求,点"取消"就地收起,
 * 不依赖 window.confirm 这个不可靠的浏览器 API。
 */

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RecoTab } from "@/app/admin/page";
import type { GetJson } from "@/lib/api-v1";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

type SlipsResp = GetJson<"/api/v1/admin/reco/slips">;
type Slip = SlipsResp["slips"][number];
type PreviewResp = GetJson<"/api/v1/admin/reco/slips/{slip_id}/preview">;

function baseSlip(overrides: Partial<Slip> = {}): Slip {
  return {
    id: "slip-1",
    slip_date: "2026-08-16",
    title: "测试推荐单",
    note: "思路说明",
    combo_type: "single",
    status: "draft",
    result: null,
    return_units: null,
    published_at: null,
    settled_at: null,
    settle_source: null,
    edit_count: 0,
    last_edited_at: "2026-08-16T08:00:00Z",
    board: "daily_pick",
    legs: [
      {
        id: "leg-1",
        match_id: 9001,
        match_desc: "主队 vs 客队 08-16 20:00",
        market: "1x2",
        selection: "主胜",
        odds: 1.9,
        result: null,
        entry_type: "provenance_bound",
        match_result: null,
        corners: null,
        needs_review: false,
        needs_review_reason: null,
      },
    ],
    ...overrides,
  } as Slip;
}

type Call = { url: string; method: string; body: unknown };
type CandidatesResp = GetJson<"/api/v1/admin/reco/match-candidates">;
type OddsOptionsResp = GetJson<"/api/v1/admin/reco/match-candidates/{match_id}/odds-options">;

/** 统一的 fetch 路由 mock——按 URL/方法分发,记录每次调用供断言。 */
function mockFetch(handlers: {
  slips?: SlipsResp;
  preview?: PreviewResp["slip"];
  candidates?: CandidatesResp["matches"];
  oddsOptions?: OddsOptionsResp["options"];
} = {}): Call[] {
  const calls: Call[] = [];
  const slipsResp: SlipsResp = handlers.slips ?? { total: 0, slips: [] };
  const impl = vi.fn((input: unknown, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });

    if (method === "GET" && url.includes("/preview")) {
      return Promise.resolve(
        new Response(JSON.stringify({ slip: handlers.preview }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    }
    if (method === "GET" && url.includes("/odds-options")) {
      return Promise.resolve(
        new Response(JSON.stringify({ match_id: 0, options: handlers.oddsOptions ?? [] }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    }
    if (method === "GET" && url.includes("/api/v1/admin/reco/match-candidates")) {
      return Promise.resolve(
        new Response(JSON.stringify({ matches: handlers.candidates ?? [] }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    }
    if (method === "GET" && url.includes("/api/v1/admin/audit-logs")) {
      return Promise.resolve(
        new Response(JSON.stringify({ logs: [] }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    }
    if (method === "GET" && url.includes("/api/v1/admin/reco/slips")) {
      return Promise.resolve(
        new Response(JSON.stringify(slipsResp), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    }
    // publish / settle / void / patch / create 等写操作:统一回一个通用
    // 成功体。warnings: []——create/edit 响应形状(2026-09 跨板块盘口冲突
    // 提示新增)都含这个字段,不带会让 r.warnings.length 在真实调用路径里
    // 抛错,即便当前这条断言不关心 warnings 内容。
    return Promise.resolve(
      new Response(JSON.stringify({ status: "ok", id: "slip-new", warnings: [] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
  });
  vi.stubGlobal("fetch", impl);
  return calls;
}

describe("筛选栏(状态 + 日期区间)", () => {
  it("状态/起始日期/结束日期变化时,列表请求正确带上对应查询参数", async () => {
    const calls = mockFetch({ slips: { total: 0, slips: [] } });
    render(<RecoTab />);

    await waitFor(() =>
      expect(
        calls.some((c) => c.method === "GET" && c.url.includes("/api/v1/admin/reco/slips") && !c.url.includes("/preview")),
      ).toBe(true),
    );

    fireEvent.change(screen.getByLabelText("状态筛选"), { target: { value: "published" } });
    await waitFor(() => expect(calls.some((c) => c.url.includes("status=published"))).toBe(true));

    fireEvent.change(screen.getByLabelText("起始日期"), { target: { value: "2026-08-01" } });
    await waitFor(() => expect(calls.some((c) => c.url.includes("date_from=2026-08-01"))).toBe(true));

    fireEvent.change(screen.getByLabelText("结束日期"), { target: { value: "2026-08-31" } });
    await waitFor(() => expect(calls.some((c) => c.url.includes("date_to=2026-08-31"))).toBe(true));

    // 三次筛选变化都应该保留之前已选的条件,不是互相覆盖成只剩最后一个参数。
    const lastListCall = [...calls]
      .reverse()
      .find((c) => c.method === "GET" && c.url.includes("/api/v1/admin/reco/slips") && !c.url.includes("/preview"))!;
    expect(lastListCall.url).toContain("status=published");
    expect(lastListCall.url).toContain("date_from=2026-08-01");
    expect(lastListCall.url).toContain("date_to=2026-08-31");
  });
});

describe("每日公推板块(2026-09 新增)", () => {
  it("筛选栏板块变化时列表请求带上 board 查询参数", async () => {
    const calls = mockFetch({ slips: { total: 0, slips: [] } });
    render(<RecoTab />);

    await waitFor(() =>
      expect(calls.some((c) => c.method === "GET" && c.url.includes("/api/v1/admin/reco/slips"))).toBe(true),
    );

    fireEvent.change(screen.getByLabelText("板块筛选"), { target: { value: "daily_public" } });
    await waitFor(() => expect(calls.some((c) => c.url.includes("board=daily_public"))).toBe(true));
  });

  it("新建表单默认板块是每日精选,选择每日公推后创建请求带上 board 字段", async () => {
    const calls = mockFetch({ slips: { total: 0, slips: [] } });
    render(<RecoTab />);

    const boardSelect = screen.getByLabelText("板块") as HTMLSelectElement;
    expect(boardSelect.value).toBe("daily_pick");

    fireEvent.change(boardSelect, { target: { value: "daily_public" } });
    expect(screen.getByText(/任何人不登录都能看到全文/)).not.toBeNull();

    fireEvent.change(screen.getByPlaceholderText("如:今日三串一"), { target: { value: "测试公推单" } });
    fireEvent.change(screen.getByPlaceholderText("比赛(搜索队名,或手动填写描述)"), {
      target: { value: "A队 vs B队 09-01 20:00" },
    });
    fireEvent.change(screen.getByPlaceholderText("玩法"), { target: { value: "1x2" } });
    fireEvent.change(screen.getByPlaceholderText("选项(如 主胜)"), { target: { value: "主胜" } });
    fireEvent.change(screen.getByPlaceholderText("赔率"), { target: { value: "1.9" } });
    fireEvent.click(screen.getByRole("button", { name: "创建草稿" }));

    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.method === "POST" &&
            c.url.includes("/api/v1/admin/reco/slips") &&
            !c.url.includes("access-grants") &&
            (c.body as { board?: string })?.board === "daily_public",
        ),
      ).toBe(true),
    );
  });

  it("列表行展示板块徽标", async () => {
    mockFetch({ slips: { total: 1, slips: [baseSlip({ board: "daily_public" })] } });
    render(<RecoTab />);
    // "每日公推"同时出现在板块 <select> 的 <option> 里,用 data-board 属性
    // 精确定位行卡片上的徽标,避免和表单选项撞上导致 getByText 命中多个元素。
    await waitFor(() =>
      expect(document.querySelector('[data-board="daily_public"]')).not.toBeNull(),
    );
  });

  it("已发布的公推单编辑面板里板块选择器被禁用", async () => {
    mockFetch({
      slips: {
        total: 1,
        slips: [baseSlip({ status: "published", board: "daily_public" })],
      },
    });
    render(<RecoTab />);
    await waitFor(() => expect(screen.getByText(/测试推荐单/)).not.toBeNull());

    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    const editBoardSelect = screen.getByLabelText("编辑板块") as HTMLSelectElement;
    expect(editBoardSelect.disabled).toBe(true);
    expect(screen.getByText("已公开发布,不能改回精选")).not.toBeNull();
  });
});

describe("发布二次确认(站内面板,2026-08-19 起不再用 window.confirm)", () => {
  it("点击「发布」不立即调用发布接口——先展开站内确认面板,不请求网络", async () => {
    const slip = baseSlip({ id: "slip-publish", status: "draft" });
    const calls = mockFetch({ slips: { total: 1, slips: [slip] } });
    render(<RecoTab />);
    await screen.findByText(/测试推荐单/);

    fireEvent.click(screen.getByRole("button", { name: "发布" }));

    // 面板展开,带上标题以便核对是哪一条(与生产事故复现一致:多条精选同屏时
    // 不能点错行却看不出点的是哪条)。
    await screen.findByText(/确认发布推荐单「测试推荐单」/);
    expect(calls.some((c) => c.url.includes("/publish"))).toBe(false);
  });

  it('点"取消"就地收起面板,不发请求(与既有作废/结算面板同一套交互)', async () => {
    const slip = baseSlip({ id: "slip-publish", status: "draft" });
    const calls = mockFetch({ slips: { total: 1, slips: [slip] } });
    render(<RecoTab />);
    await screen.findByText(/测试推荐单/);

    fireEvent.click(screen.getByRole("button", { name: "发布" }));
    await screen.findByText(/确认发布推荐单/);

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.queryByText(/确认发布推荐单/)).toBeNull());
    expect(calls.some((c) => c.url.includes("/publish"))).toBe(false);
  });

  it('面板里点"确认发布"才真正调用发布接口', async () => {
    const slip = baseSlip({ id: "slip-publish", status: "draft" });
    const calls = mockFetch({ slips: { total: 1, slips: [slip] } });
    render(<RecoTab />);
    await screen.findByText(/测试推荐单/);

    fireEvent.click(screen.getByRole("button", { name: "发布" }));
    await screen.findByText(/确认发布推荐单/);
    fireEvent.click(screen.getByRole("button", { name: "确认发布" }));

    await waitFor(() =>
      expect(calls.some((c) => c.method === "POST" && c.url.includes("/slip-publish/publish"))).toBe(true),
    );
  });

  it("后端拒绝发布(缺乏真实溯源)时,把具体错误文案原样展示,不是笼统的「操作失败」", async () => {
    const slip = baseSlip({ id: "slip-reject", status: "draft" });
    const calls = mockFetch({ slips: { total: 1, slips: [slip] } });
    // 覆写 publish 调用,回一个 400 + 具体错误文案(与后端真实契约一致)。
    const originalFetch = (globalThis as { fetch: typeof fetch }).fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: unknown, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/publish")) {
          calls.push({ url, method: "POST", body: undefined });
          return Promise.resolve(
            new Response(
              JSON.stringify({
                code: "HTTP_400",
                message: "以下腿缺乏真实盘口溯源(entry_type=legacy_manual),不允许发布,请从真实比赛/真实盘口重新选择后再发布: 第1条(主队 vs 客队 08-16 20:00 / 主胜)",
                details: null,
              }),
              { status: 400, headers: { "content-type": "application/json" } },
            ),
          );
        }
        return originalFetch(input as never, init);
      }),
    );
    render(<RecoTab />);
    await screen.findByText(/测试推荐单/);

    fireEvent.click(screen.getByRole("button", { name: "发布" }));
    await screen.findByText(/确认发布推荐单/);
    fireEvent.click(screen.getByRole("button", { name: "确认发布" }));

    await screen.findByText(/以下腿缺乏真实盘口溯源/);
  });
});

describe("编辑(复用既有 PATCH,不新建端点)", () => {
  it("编辑表单提交调用 PATCH /api/v1/admin/reco/slips/{id},body 带上修改后的标题", async () => {
    const slip = baseSlip({ id: "slip-edit", status: "draft" });
    const calls = mockFetch({ slips: { total: 1, slips: [slip] } });
    render(<RecoTab />);
    await screen.findByText(/测试推荐单/);

    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    const panel = screen.getByTestId("edit-panel");
    const titleInput = within(panel).getByDisplayValue("测试推荐单");
    fireEvent.change(titleInput, { target: { value: "修改后的标题" } });
    fireEvent.click(within(panel).getByRole("button", { name: "保存编辑" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    const patchCall = calls.find((c) => c.method === "PATCH")!;
    expect(patchCall.url).toBe("/api/v1/admin/reco/slips/slip-edit");
    expect(patchCall.body).toMatchObject({ title: "修改后的标题" });
    // 不应该出现任何形如 .../edit 的平行新端点。
    expect(calls.some((c) => c.url.includes("/slip-edit/edit"))).toBe(false);
  });

  it("只编辑标题时不提交 legs 字段——不会因为无关编辑而误伤已有腿的真实盘口溯源", async () => {
    const slip = baseSlip({ id: "slip-edit-2", status: "draft" });
    const calls = mockFetch({ slips: { total: 1, slips: [slip] } });
    render(<RecoTab />);
    await screen.findByText(/测试推荐单/);

    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    const panel = screen.getByTestId("edit-panel");
    fireEvent.click(within(panel).getByRole("button", { name: "保存编辑" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    const patchCall = calls.find((c) => c.method === "PATCH")!;
    expect(patchCall.body).not.toHaveProperty("legs");
  });
});

describe("待确认标记(needs_review)", () => {
  it("needs_review=true 的腿渲染出「待确认」,needs_review=false 的腿不渲染", async () => {
    const slip = baseSlip({
      id: "slip-review",
      status: "published",
      legs: [
        {
          id: "leg-a",
          match_id: 9001,
          match_desc: "已完赛队 vs 对手队 08-16 20:00",
          market: "1x2",
          selection: "主胜",
          odds: 1.9,
          result: null,
          entry_type: "provenance_bound",
          match_result: null,
          corners: null,
          needs_review: true,
          needs_review_reason: "比赛已完赛,等待下一轮自动结算任务处理或人工结算",
        },
        {
          id: "leg-b",
          match_id: 9002,
          match_desc: "未开赛队 vs 对手队 08-20 20:00",
          market: "1x2",
          selection: "客胜",
          odds: 2.1,
          result: null,
          entry_type: "provenance_bound",
          match_result: null,
          corners: null,
          needs_review: false,
          needs_review_reason: null,
        },
      ],
    });
    mockFetch({ slips: { total: 1, slips: [slip] } });
    render(<RecoTab />);
    await screen.findByText(/测试推荐单/);

    const reviewMarks = screen.getAllByText("待确认");
    expect(reviewMarks).toHaveLength(1);
    // 待确认标记必须挂在 leg-a 所在的行内,不是随便挂在卡片顶层。
    const legARow = reviewMarks[0].closest("li")!;
    expect(within(legARow).getByText(/已完赛队/)).not.toBeNull();

    // 绝不能和「未中」使用同一套视觉——不应该被识别成 lose 结果文案。
    expect(screen.queryByText("未中")).toBeNull();
  });
});

describe("会员预览", () => {
  it("点击「会员预览」调用 GET /admin/reco/slips/{id}/preview,并把会员可见字段与仅后台可见字段分开展示", async () => {
    const slip = baseSlip({ id: "slip-preview", status: "draft" });
    const previewSlip: PreviewResp["slip"] = {
      id: "slip-preview",
      slip_date: "2026-08-16",
      title: "测试推荐单",
      note: "思路说明",
      combo_type: "single",
      status: "draft",
      result: null,
      return_units: null,
      published_at: null,
      settled_at: null,
      edit_count: 0,
      last_edited_at: "2026-08-16T08:00:00Z",
      legs: [
        {
          id: "leg-1",
          match_id: 9001,
          match_desc: "主队 vs 客队 08-16 20:00",
          market: "1x2",
          selection: "主胜",
          odds: 1.9,
          result: null,
        },
      ],
    };
    const calls = mockFetch({ slips: { total: 1, slips: [slip] }, preview: previewSlip });
    render(<RecoTab />);
    await screen.findByText(/测试推荐单/);

    fireEvent.click(screen.getByRole("button", { name: "会员预览" }));

    await waitFor(() =>
      expect(calls.some((c) => c.method === "GET" && c.url === "/api/v1/admin/reco/slips/slip-preview/preview")).toBe(
        true,
      ),
    );

    const memberView = await screen.findByTestId("preview-member-view");
    expect(within(memberView).getByText("测试推荐单")).not.toBeNull();
    expect(within(memberView).getByText(/主胜/)).not.toBeNull();

    const adminOnly = screen.getByTestId("preview-admin-only");
    // entry_type 这类运营字段只出现在"仅后台可见"区块,不出现在会员可见区块。
    expect(within(adminOnly).getByText(/真实盘口溯源/)).not.toBeNull();
    expect(within(memberView).queryByText(/真实盘口溯源/)).toBeNull();
  });
});

describe("分页", () => {
  it("上一页/下一页按钮根据 total/limit/offset 正确禁用,并请求对应 offset", async () => {
    const manySlips: Slip[] = Array.from({ length: 5 }, (_, i) =>
      baseSlip({ id: `slip-${i}`, title: `第${i}单` }),
    );
    const calls = mockFetch({ slips: { total: 45, slips: manySlips } });
    render(<RecoTab />);
    await screen.findByText(/第0单/);

    // total=45,limit=20 → 第 1/3 页,上一页在第一页应禁用。
    expect(screen.getByText(/第 1 \/ 3 页/)).not.toBeNull();
    expect((screen.getByRole("button", { name: "上一页" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "下一页" }) as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("offset=20"))).toBe(true));
  });
});

describe("盘口选项展示(港盘/十进制说明 + 新鲜度)", () => {
  it("真实盘口选项下拉用中文说明 odds_format 与非新鲜状态,不暴露内部枚举值拼写", async () => {
    const candidates: CandidatesResp["matches"] = [
      {
        match_id: 5001, league_id: 47, league_name: "英超",
        home_name: "主队FC", away_name: "客队FC",
        kickoff_at_utc: "2026-08-20T19:00:00Z", status: "NotStarted",
      },
    ];
    const oddsOptions: OddsOptionsResp["options"] = [
      {
        market: "1x2", market_label: "胜平负", selection: "主胜", odds: 1.9,
        company_name: "Bet365", observed_at: "2026-08-16T08:00:00Z",
        snapshot_id: 1, company_id: "b365", line: null, side: "home",
        odds_format: "decimal", freshness: "FRESH",
      },
      {
        market: "ou", market_label: "大小球", selection: "大2.5", odds: 0.9,
        company_name: "Crown", observed_at: "2026-08-10T08:00:00Z",
        snapshot_id: 2, company_id: "crown", line: 2.5, side: "over",
        odds_format: "hk", freshness: "STALE",
      },
    ];
    mockFetch({ slips: { total: 0, slips: [] }, candidates, oddsOptions });
    render(<RecoTab />);
    await screen.findByText("新建推荐单(草稿)");

    const matchInput = screen.getByPlaceholderText("比赛(搜索队名,或手动填写描述)");
    fireEvent.focus(matchInput);
    const candidateButton = await screen.findByRole("button", { name: /主队FC vs 客队FC/ });
    fireEvent.click(candidateButton);

    await screen.findByText("真实盘口选项…");
    const selectEl = screen.getByText("真实盘口选项…").closest("select")!;
    const optionTexts = Array.from(selectEl.querySelectorAll("option")).map((o) => o.textContent ?? "");

    expect(optionTexts.some((t) => t.includes("十进制"))).toBe(true);
    expect(optionTexts.some((t) => t.includes("港盘"))).toBe(true);
    // STALE 复用既有 syncStateLabel 中文说明,不新写一套文案。
    expect(optionTexts.some((t) => t.includes("数据等待刷新"))).toBe(true);
    // FRESH 不啰嗦展示(与 MatchRow.tsx 既有克制风格一致)。
    expect(optionTexts.some((t) => t.includes("数据已更新"))).toBe(false);
    // 不直接暴露内部枚举值拼写。
    expect(optionTexts.some((t) => /\bhk\b|\bdecimal\b|\bSTALE\b|\bFRESH\b/.test(t))).toBe(false);
  });
});
