/**
 * Admin 用户管理 tab:授权表单默认套餐值(2026-08-16 权限口径修正)。
 *
 * grantPlan/planId 曾硬编码默认值 "pro"——这个 plan id 引用一个已下架的
 * 套餐(is_active=0),不在 /api/v1/products 返回的可授权套餐列表里。默认值
 * 必须不预设任何已下架套餐,理想情况下应该指向一个真实存在的 plan_id。
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AdminPage from "@/app/admin/page";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const PLANS = [
  { id: "free", name_zh: "游客", rank: 0 },
  { id: "member", name_zh: "注册用户", rank: 0 },
  { id: "daily_picks", name_zh: "精选授权用户", rank: 1 },
];

function mockFetchByUrl(
  routes: Record<string, unknown>,
  onRequest?: (url: string, init?: RequestInit) => void,
) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: unknown, init?: RequestInit) => {
      const url = String(input);
      onRequest?.(url, init);
      for (const [suffix, body] of Object.entries(routes)) {
        if (url.includes(suffix)) {
          const headers = new Headers();
          headers.set("content-type", "application/json");
          return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers }));
        }
      }
      throw new Error(`unmocked fetch: ${url}`);
    }),
  );
}

describe("Admin 用户管理:授权表单不得默认选中已下架的 pro 套餐", () => {
  it("不触碰下拉框直接提交'确认开通'时,请求体里的 plan_id 必须是真实套餐,不是已下架的 pro", async () => {
    const grantCalls: { url: string; body: string }[] = [];
    mockFetchByUrl(
      {
        "/api/v1/me": {
          authenticated: true,
          user: { id: "u1", display_name: "站长", role: "admin" },
          plan: "member",
          entitlements: [],
        },
        "/api/v1/products": { plans: PLANS, products: [] },
        "/api/v1/admin/users": {
          users: [
            {
              id: "user-1",
              display_name: "测试用户",
              role: "user",
              status: "active",
              plan_id: "member",
              plan_ends_at: null,
              created_at: "2026-08-01T00:00:00Z",
              last_login_at: null,
            },
          ],
          total: 1,
        },
      },
      (url, init) => {
        if (url.includes("/grant") && init?.method === "POST") {
          grantCalls.push({ url, body: String(init.body ?? "") });
        }
      },
    );

    render(<AdminPage />);
    await waitFor(() => expect(screen.queryByText("测试用户")).not.toBeNull());

    screen.getAllByText("开通订阅")[0].click();
    await waitFor(() => expect(screen.queryByText("确认开通")).not.toBeNull());

    // 刻意不触碰下拉框,直接提交——这是最容易暴露"内部状态仍是硬编码默认值"
    // 的路径(下拉框视觉上可能显示第一个真实选项,但受控组件的 state 才是
    // 真正提交的值)。"确认开通"本身就是 grantFor 展开后的第二步确认,
    // 不再经 window.confirm(2026-08-21 起去掉这层不可靠的原生弹窗)。
    screen.getByText("确认开通").click();

    await waitFor(() => expect(grantCalls.length).toBeGreaterThan(0));
    const body = JSON.parse(grantCalls[0].body) as { plan_id: string };
    expect(body.plan_id).not.toBe("pro");
    expect(PLANS.some((p) => p.id === body.plan_id)).toBe(true);
  });
});
