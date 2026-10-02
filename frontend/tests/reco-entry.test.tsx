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
const PUBLIC = { window_days: 7, slips: [] };

const TRACK_WITH_DATA = {
  summary: {
    settled_count: 33, win_count: 20, lose_count: 11, push_count: 2, half_win_count: 0,
    half_loss_count: 0, voided_count: 1, hit_rate: 0.645, net_units: 7.67,
  },
  total: 0,
  slips: [],
};

const CURVE = [
  { slip_date: "2026-09-01", net_units: 0.9, cum_units: 0.9 },
  { slip_date: "2026-09-02", net_units: -1, cum_units: -0.1 },
  { slip_date: "2026-09-03", net_units: 0.85, cum_units: 0.75 },
];

describe("/reco 顶部", () => {
  it("说明句、三步说明都已删除(站长 2026-10-01:没人会看)", async () => {
    mockFetch({
      "/api/v1/me": { body: ME_ANON },
      "/api/v1/reco/track-record": { body: TRACK_WITH_DATA },
      "/api/v1/reco/public": { body: PUBLIC },
    });
    const { container } = render(<RecoPage />);
    await screen.findByTestId("reco-summary");
    expect(container.textContent).not.toContain("结算后的每一单都公开");
    expect(container.textContent).not.toContain("每天人工精选，开通后在这里查看。");
    expect(screen.queryByTestId("reco-unlock-steps")).toBeNull();
    expect(container.textContent).not.toContain("结算完的单子都在这儿");
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

  it("没有已结算样本时不放战绩摘要、不放盈利走势(不显示一排 0)", async () => {
    mockFetch({
      "/api/v1/me": { body: ME_ANON },
      "/api/v1/reco/track-record": { body: { ...TRACK, curve: [] } },
      "/api/v1/reco/public": { body: PUBLIC },
    });
    render(<RecoPage />);
    await waitFor(() =>
      expect(vi.mocked(fetch).mock.calls.some((c) => String(c[0]).includes("track-record"))).toBe(true),
    );
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId("reco-summary")).toBeNull();
    expect(screen.queryByTestId("profit-curve")).toBeNull();
  });

  it("盈利走势:按每单 500 元、本金 10000 元换算,文字与最后一点一致", async () => {
    mockFetch({
      "/api/v1/me": { body: ME_ANON },
      "/api/v1/reco/track-record": { body: { ...TRACK_WITH_DATA, curve: CURVE } },
      "/api/v1/reco/public": { body: PUBLIC },
    });
    render(<RecoPage />);
    const card = await screen.findByTestId("profit-curve");
    expect(card.textContent).toContain("每单 500 元、本金 10,000 元");
    expect(card.textContent).toContain("+375 元");       // 0.75 单位 × 500
    expect(card.textContent).toContain("10,375");
    expect(card.textContent).toContain("+3.8%");
    // 2026-10-02 站长:不放回撤/时间范围/风险提示这类说明句
    expect(card.textContent).not.toContain("回落");
    expect(card.textContent).not.toContain("过去的结果");
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
