/**
 * /reco 顶部入口(2026-09-26):两段说明合并成一句"每天人工精选，开通后在这里查看。";
 * 未登录 → "登录"按钮;已登录且还没有任何授权 → "怎么开通"(跳 /pricing 的开通说明);
 * 已开通、或授权状态没查到 → 都不放按钮(接口失败不能告诉用户"你没开通")。
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

describe("/reco 顶部入口", () => {
  it("两段说明合并成一句;旧的两段文字都不在了", async () => {
    mockFetch({
      "/api/v1/me": { body: ME_ANON },
      "/api/v1/reco/track-record": { body: TRACK },
      "/api/v1/reco/public": { body: PUBLIC },
    });
    const { container } = render(<RecoPage />);
    await screen.findByText("每天人工精选，开通后在这里查看。");
    expect(container.textContent).not.toContain("每天人工出的推荐");
    expect(container.textContent).not.toContain("不需要登录，任何人都能看到全部内容");
  });

  it("未登录:一句话下面有'登录'按钮,指向 /login?next=/reco", async () => {
    mockFetch({
      "/api/v1/me": { body: ME_ANON },
      "/api/v1/reco/track-record": { body: TRACK },
      "/api/v1/reco/public": { body: PUBLIC },
    });
    render(<RecoPage />);
    const btn = await screen.findByRole("link", { name: "登录" });
    expect(btn.getAttribute("href")).toBe("/login?next=/reco");
    expect(screen.queryByRole("link", { name: "怎么开通" })).toBeNull();
  });

  it("已登录但没有任何 active 授权:显示'怎么开通',跳 /pricing#how-to-unlock;不再有'登录'按钮", async () => {
    mockFetch({
      "/api/v1/me": { body: ME_USER },
      "/api/v1/reco/track-record": { body: TRACK },
      "/api/v1/reco/public": { body: PUBLIC },
      "/api/v1/reco/my-access": { body: { grants: [{ id: "g1", status: "revoked" }] } },
      "/api/v1/reco/daily": { body: DAILY },
    });
    render(<RecoPage />);
    const btn = await screen.findByRole("link", { name: "怎么开通" });
    expect(btn.getAttribute("href")).toBe("/pricing#how-to-unlock");
    expect(screen.queryByRole("link", { name: "登录" })).toBeNull();
  });

  it("已登录且有 active 授权:什么按钮都不放", async () => {
    mockFetch({
      "/api/v1/me": { body: ME_USER },
      "/api/v1/reco/track-record": { body: TRACK },
      "/api/v1/reco/public": { body: PUBLIC },
      "/api/v1/reco/my-access": { body: { grants: [{ id: "g1", status: "active" }] } },
      "/api/v1/reco/daily": { body: DAILY },
    });
    render(<RecoPage />);
    await screen.findByText("每天人工精选，开通后在这里查看。");
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some((c) => String(c[0]).includes("my-access"))).toBe(true));
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByRole("link", { name: "怎么开通" })).toBeNull();
    expect(screen.queryByRole("link", { name: "登录" })).toBeNull();
  });

  it("授权状态接口失败:不放'怎么开通'(不能因为接口出错就说用户没开通)", async () => {
    mockFetch({
      "/api/v1/me": { body: ME_USER },
      "/api/v1/reco/track-record": { body: TRACK },
      "/api/v1/reco/public": { body: PUBLIC },
      "/api/v1/reco/my-access": { status: 500, body: { code: "boom" } },
      "/api/v1/reco/daily": { body: DAILY },
    });
    render(<RecoPage />);
    await screen.findByText("每天人工精选，开通后在这里查看。");
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some((c) => String(c[0]).includes("my-access"))).toBe(true));
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByRole("link", { name: "怎么开通" })).toBeNull();
  });
});
