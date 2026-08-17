/**
 * SiteNav 渲染测试(2026-08-16 权限口径修正)。
 *
 * 顶栏账户区域此前显示一个来自 me.plan 的免费/会员/精选徽标——按 §五
 * 文案要求,顶部不得展示 Premium/套餐身份,普通登录状态应显示"已登录"
 * 或直接显示用户名,不展示这个通用 plan 徽标。
 */
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SiteNav } from "@/components/SiteNav";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function mockMeFetch(body: unknown) {
  const headers = new Headers();
  headers.set("content-type", "application/json");
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status: 200, headers })),
  );
  // ThemeToggle 读取 matchMedia,jsdom 默认不实现。
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockReturnValue({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }),
  );
}

describe("SiteNav:登录状态不再展示套餐徽标", () => {
  it("普通登录用户不出现'会员'/'免费'/'精选'徽标,直接显示用户名", async () => {
    mockMeFetch({
      authenticated: true,
      user: { id: "u1", display_name: "张三", role: "user" },
      plan: "member",
      entitlements: [],
    });
    render(<SiteNav />);

    await waitFor(() => expect(screen.getAllByText("张三").length).toBeGreaterThan(0));
    // 精确定位到账户链接本身,不误伤"每日精选"导航项/底部导航"精选"标签。
    const accountLink = screen.getByRole("link", { name: "张三" });
    expect(within(accountLink).queryByText("会员")).toBeNull();
    expect(within(accountLink).queryByText("免费")).toBeNull();
    expect(within(accountLink).queryByText("精选")).toBeNull();
    expect(accountLink.querySelector("span")).toBeNull();
  });
});
