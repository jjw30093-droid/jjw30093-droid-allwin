/**
 * /reco 顶部(2026-10-01 站长 P0 改版;2026-09-26 的入口规则保留):
 * - 战绩摘要(已结算/命中率/盈利)放最上面,所有人可见;
 * - "怎么看今日精选"三步说明:未登录 → 去登录;已登录但没有任何 active 授权 → 查看我的编号;
 *   已开通、或授权状态没查到 → 不显示(接口失败不能告诉用户"你没开通");
 * - 默认标签:公推这 7 天有内容 → 每日公推;一条都没有 → 历史战绩(不再落在空白页)。
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import RecoPage from "@/app/reco/page";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const TRACK = {
  summary: {
    settled_count: 0, win_count: 0, lose_count: 0, push_count: 0, half_win_count: 0,
    half_loss_count: 0, voided_count: 0, hit_rate: null, net_units: 0,
  },
  total: 0,
  slips: [],
};

function mockFetch(routes: Record<string, { status?: number; body: unknown }>) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: unknown) => {
      const url = String(input);
      for (const [suffix, r] of Object.entries(routes)) {
        if (url.includes(suffix)) {
          const headers = new Headers({ "content-type": "application/json" });
          return Promise.resolve(new Response(JSON.stringify(r.body), { status: r.status ?? 200, headers }));
        }
      }
      return Promise.resolve(new Response(JSON.stringify({ code: "not_found" }), { status: 404 }));
    }),
  );
}

const ME_ANON = { authenticated: false, plan: "free", entitlements: [] };
const ME_USER = { authenticated: true, user: { id: "u1", display_name: "测试", role: "user" }, plan: "free", entitlements: [] };
const PUBLIC = { window_days: 7, slips: [] };
const DAILY = { window_days: 30, slips: [] };

const TRACK_WITH_DATA = {
  summary: {
    settled_count: 33, win_count: 20, lose_count: 11, push_count: 2, half_win_count: 0,
    half_loss_count: 0, voided_count: 1, hit_rate: 0.645, net_units: 7.67,
  },
  total: 0,
  slips: [],
};

describe("/reco 顶部", () => {
  it("新一句说明;旧说明不在了", async () => {
    mockFetch({
      "/api/v1/me": { body: ME_ANON },
      "/api/v1/reco/track-record": { body: TRACK },
      "/api/v1/reco/public": { body: PUBLIC },
    });
    const { container } = render(<RecoPage />);
    await screen.findByText("每天人工挑的比赛，结算后的每一单都公开，中没中都留着。");
    expect(container.textContent).not.toContain("每天人工精选，开通后在这里查看。");
  });

  it("战绩摘要放在最上面:已结算 33、命中率 64.5%、盈利 +7.67", async () => {
    mockFetch({
      "/api/v1/me": { body: ME_ANON },
      "/api/v1/reco/track-record": { body: TRACK_WITH_DATA },
      "/api/v1/reco/public": { body: PUBLIC },
    });
    render(<RecoPage />);
    const box = await screen.findByTestId("reco-summary");
    expect(box.textContent).toContain("33");
    expect(box.textContent).toContain("64.5%");
    expect(box.textContent).toContain("+7.67");
    expect(box.getAttribute("href")).toBe("/reco?tab=record");
  });

  it("没有已结算样本时不放战绩摘要(不显示一排 0)", async () => {
    mockFetch({
      "/api/v1/me": { body: ME_ANON },
      "/api/v1/reco/track-record": { body: TRACK },
      "/api/v1/reco/public": { body: PUBLIC },
    });
    render(<RecoPage />);
    await screen.findByText("每天人工挑的比赛，结算后的每一单都公开，中没中都留着。");
    expect(screen.queryByTestId("reco-summary")).toBeNull();
  });

  it("未登录:三步说明 + '去登录' 指向 /login?next=/reco", async () => {
    mockFetch({
      "/api/v1/me": { body: ME_ANON },
      "/api/v1/reco/track-record": { body: TRACK },
      "/api/v1/reco/public": { body: PUBLIC },
    });
    render(<RecoPage />);
    const steps = await screen.findByTestId("reco-unlock-steps");
    expect(steps.textContent).toContain("用微信验证码登录");
    expect(steps.textContent).toContain("6 位用户编号");
    expect(screen.getByRole("link", { name: "去登录" }).getAttribute("href")).toBe("/login?next=/reco");
    expect(screen.getByRole("link", { name: "怎么联系我们" }).getAttribute("href")).toBe("/pricing#how-to-unlock");
    expect(screen.queryByRole("link", { name: "查看我的编号" })).toBeNull();
  });

  it("已登录但没有任何 active 授权:三步说明里第一步已完成,按钮换成'查看我的编号'", async () => {
    mockFetch({
      "/api/v1/me": { body: ME_USER },
      "/api/v1/reco/track-record": { body: TRACK },
      "/api/v1/reco/public": { body: PUBLIC },
      "/api/v1/reco/my-access": { body: { grants: [{ id: "g1", status: "revoked" }] } },
      "/api/v1/reco/daily": { body: DAILY },
    });
    render(<RecoPage />);
    const btn = await screen.findByRole("link", { name: "查看我的编号" });
    expect(btn.getAttribute("href")).toBe("/account");
    expect(screen.getByTestId("reco-unlock-steps").textContent).toContain("✓");
    expect(screen.queryByRole("link", { name: "去登录" })).toBeNull();
  });

  it("已登录且有 active 授权:不显示三步说明", async () => {
    mockFetch({
      "/api/v1/me": { body: ME_USER },
      "/api/v1/reco/track-record": { body: TRACK },
      "/api/v1/reco/public": { body: PUBLIC },
      "/api/v1/reco/my-access": { body: { grants: [{ id: "g1", status: "active" }] } },
      "/api/v1/reco/daily": { body: DAILY },
    });
    render(<RecoPage />);
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some((c) => String(c[0]).includes("my-access"))).toBe(true));
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId("reco-unlock-steps")).toBeNull();
  });

  it("授权状态接口失败:不显示三步说明(不能因为接口出错就说用户没开通)", async () => {
    mockFetch({
      "/api/v1/me": { body: ME_USER },
      "/api/v1/reco/track-record": { body: TRACK },
      "/api/v1/reco/public": { body: PUBLIC },
      "/api/v1/reco/my-access": { status: 500, body: { code: "boom" } },
      "/api/v1/reco/daily": { body: DAILY },
    });
    render(<RecoPage />);
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some((c) => String(c[0]).includes("my-access"))).toBe(true));
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId("reco-unlock-steps")).toBeNull();
  });
});

describe("/reco 默认标签", () => {
  it("公推这 7 天一条都没有:默认落在「历史战绩」,不显示'还没发过公推'", async () => {
    mockFetch({
      "/api/v1/me": { body: ME_ANON },
      "/api/v1/reco/track-record": { body: TRACK_WITH_DATA },
      "/api/v1/reco/public": { body: PUBLIC },
    });
    const { container } = render(<RecoPage />);
    await waitFor(() =>
      expect(screen.getByRole("link", { name: "历史战绩" }).getAttribute("aria-current")).toBe("page"),
    );
    expect(container.textContent).not.toContain("还没发过公推");
  });
});
