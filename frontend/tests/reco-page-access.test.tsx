/**
 * /reco 页面按场授权渲染测试(2026-08-16 产品权限口径修正)。
 *
 * 覆盖三种状态,对应任务验收标准:
 * 1. 未登录:引导登录,不发起 /api/v1/reco/daily 请求,不渲染任何 slip 内容。
 * 2. 已登录但对某一场无权限(access_required=true):渲染固定中性文案,
 *    页面文本不包含任何腿/赔率/标题文本(后端投影本来就不下发这些字段)。
 * 3. 已登录且有权限(access_required=false):渲染完整内容(标题/腿/赔率)。
 *
 * 历史战绩(2026-08-16 起匿名可见)在三种状态下都应该正常展示,不再受登录
 * 状态影响。
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import RecoPage from "@/app/reco/page";

let searchParamsValue = "";
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(searchParamsValue),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  searchParamsValue = "";
});

const EMPTY_TRACK_RECORD = {
  summary: {
    settled_count: 0,
    win_count: 0,
    lose_count: 0,
    push_count: 0,
    half_win_count: 0,
    half_loss_count: 0,
    voided_count: 0,
    hit_rate: null,
    net_units: 0,
  },
  total: 0,
  slips: [],
};

function mockFetchByUrl(routes: Record<string, unknown>) {
  const calledUrls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: unknown) => {
      const url = String(input);
      calledUrls.push(url);
      for (const [suffix, body] of Object.entries(routes)) {
        if (url.includes(suffix)) {
          const headers = new Headers();
          headers.set("content-type", "application/json");
          return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers }));
        }
      }
      return Promise.resolve(new Response(JSON.stringify({ code: "not_found" }), { status: 404 }));
    }),
  );
  return calledUrls;
}

describe("/reco 未登录:今日精选标签引导登录,不拉取也不渲染任何 slip 内容", () => {
  it("显示固定引导文案,且从不请求 /api/v1/reco/daily", async () => {
    searchParamsValue = "tab=daily";
    const calls = mockFetchByUrl({
      "/api/v1/me": { authenticated: false, plan: "free", entitlements: [] },
      "/api/v1/reco/track-record": EMPTY_TRACK_RECORD,
    });

    render(<RecoPage />);

    await waitFor(() =>
      expect(
        screen.queryByText("每日精选按场开通，登录后能看到你有哪几场。"),
      ).not.toBeNull(),
    );
    expect(screen.queryByText("前往登录")).not.toBeNull();
    expect(calls.some((u) => u.includes("/api/v1/reco/daily"))).toBe(false);
  });
});

describe("/reco 已登录但无权限:中性投影,不泄漏任何正文字段", () => {
  it("渲染固定文案,页面文本不含任何腿/赔率/标题信息", async () => {
    searchParamsValue = "tab=daily";
    mockFetchByUrl({
      "/api/v1/me": {
        authenticated: true,
        user: { id: "u1", display_name: "测试用户", role: "user" },
        plan: "member",
        entitlements: [],
      },
      "/api/v1/reco/track-record": EMPTY_TRACK_RECORD,
      "/api/v1/reco/daily": {
        window_days: 30,
        slips: [
          { id: "slip-locked-1", slip_date: "2026-08-16", status: "published", access_required: true },
        ],
      },
    });

    render(<RecoPage />);

    await waitFor(() =>
      expect(
        screen.queryByText("本场每日精选需要单独授权，当前账号暂无查看权限。"),
      ).not.toBeNull(),
    );
    // 已登录态不应显示未登录时的列表级引导文案。
    expect(
      screen.queryByText("每日精选按场开通，登录后能看到你有哪几场。"),
    ).toBeNull();
    // 存在性 + 状态允许展示。
    expect(screen.queryByText("2026-08-16")).not.toBeNull();
    // 正文字段(标题/腿/赔率/理由)物理不在响应体里,页面上也就不可能出现。
    for (const leaked of ["主胜", "客胜", "半赢", "@1.9", "@2.0"]) {
      expect(screen.queryByText(leaked)).toBeNull();
    }
  });
});

describe("/reco 已登录且有权限:完整内容", () => {
  it("渲染标题/腿/赔率等完整正文", async () => {
    searchParamsValue = "tab=daily";
    mockFetchByUrl({
      "/api/v1/me": {
        authenticated: true,
        user: { id: "u1", display_name: "测试用户", role: "user" },
        plan: "member",
        entitlements: [],
      },
      "/api/v1/reco/track-record": EMPTY_TRACK_RECORD,
      "/api/v1/reco/daily": {
        window_days: 30,
        slips: [
          {
            id: "slip-unlocked-1",
            slip_date: "2026-08-16",
            title: "已授权测试单",
            note: null,
            combo_type: "single",
            status: "published",
            result: null,
            return_units: null,
            published_at: "2026-08-16T08:00:00Z",
            settled_at: null,
            edit_count: 0,
            last_edited_at: "2026-08-16T08:00:00Z",
            legs: [
              {
                id: "leg-1",
                match_id: null,
                match_desc: "主队 vs 客队 08-20 20:00",
                market: "1x2",
                selection: "主胜",
                odds: 1.9,
                result: null,
              },
            ],
            access_required: false,
          },
        ],
      },
    });

    render(<RecoPage />);

    await waitFor(() => expect(screen.queryByText("已授权测试单")).not.toBeNull());
    expect(screen.queryByText("主胜", { exact: false })).not.toBeNull();
    expect(
      screen.queryByText("本场每日精选需要单独授权，当前账号暂无查看权限。"),
    ).toBeNull();
  });
});

describe("/reco 默认标签:每日公推(2026-09 起,导航栏「每日精选」入口点进来直接看到公推)", () => {
  it("未登录、不带 ?tab= 时默认展示每日公推,不要求登录即可看到完整内容", async () => {
    mockFetchByUrl({
      "/api/v1/me": { authenticated: false, plan: "free", entitlements: [] },
      "/api/v1/reco/track-record": EMPTY_TRACK_RECORD,
      "/api/v1/reco/public": {
        window_days: 7,
        slips: [
          {
            id: "public-slip-1",
            slip_date: "2026-09-01",
            title: "匿名可见公推单",
            note: null,
            combo_type: "single",
            status: "published",
            result: null,
            return_units: null,
            published_at: "2026-09-01T08:00:00Z",
            settled_at: null,
            edit_count: 0,
            last_edited_at: "2026-09-01T08:00:00Z",
            legs: [
              {
                id: "leg-public-1",
                match_id: null,
                match_desc: "公推队 vs 对手队 09-02 20:00",
                market: "1x2",
                selection: "主胜",
                odds: 1.85,
                result: null,
              },
            ],
          },
        ],
      },
    });

    render(<RecoPage />);

    await waitFor(() => expect(screen.queryByText("匿名可见公推单")).not.toBeNull());
    expect(screen.queryByText("主胜", { exact: false })).not.toBeNull();
    // 默认标签不应该是「今日精选」的登录引导文案。
    expect(screen.queryByText(/每日精选按场开通/)).toBeNull();
  });
});

describe("/reco 历史战绩:匿名可见,不要求登录", () => {
  it("显式 ?tab=record 时正常拉取并展示已结算记录", async () => {
    searchParamsValue = "tab=record";
    mockFetchByUrl({
      "/api/v1/me": { authenticated: false, plan: "free", entitlements: [] },
      "/api/v1/reco/track-record": {
        summary: {
          settled_count: 1,
          win_count: 1,
          lose_count: 0,
          push_count: 0,
          half_win_count: 0,
          half_loss_count: 0,
          voided_count: 0,
          hit_rate: 1,
          net_units: 0.9,
        },
        total: 1,
        slips: [
          {
            id: "slip-settled-1",
            slip_date: "2026-08-10",
            title: "匿名可见战绩单",
            note: null,
            combo_type: "single",
            status: "settled",
            result: "win",
            return_units: 1.9,
            published_at: "2026-08-10T08:00:00Z",
            settled_at: "2026-08-10T22:00:00Z",
            edit_count: 0,
            last_edited_at: "2026-08-10T22:00:00Z",
            legs: [
              {
                id: "leg-1",
                match_id: null,
                match_desc: "主队 vs 客队 08-10 20:00",
                market: "1x2",
                selection: "主胜",
                odds: 1.9,
                result: "win",
              },
            ],
          },
        ],
      },
    });

    render(<RecoPage />);

    await waitFor(() => expect(screen.queryByText("匿名可见战绩单")).not.toBeNull());
  });
});
