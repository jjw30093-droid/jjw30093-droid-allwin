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

/**
 * 「比赛」与「赛果」是同一条路由 /matches 的两种视图(靠 ?status 区分)。
 * 选中态若只看 pathname,两项会同时高亮——这不是纯视觉问题:导航是用户判断
 * "我现在在哪"的唯一依据,两个都亮等于没有指示。
 */
describe("SiteNav:比赛 / 赛果 是同一路由的两个视图,选中态必须互斥", () => {
  function renderAt(pathname: string, search: string) {
    vi.resetModules();
    vi.doMock("next/navigation", () => ({
      usePathname: () => pathname,
      useSearchParams: () => new URLSearchParams(search),
    }));
    return import("@/components/SiteNav");
  }

  it.each([
    ["", "比赛"],
    ["status=upcoming", "比赛"],
    ["status=finished", "赛果"],
  ])("/matches?%s → 只有「%s」是选中态", async (search, expectedActive) => {
    mockMeFetch({ authenticated: false });
    const { SiteNav: Nav } = await renderAt("/matches", search);
    const { container } = render(<Nav />);
    // 只看顶部主导航:底部导航的「比赛」是路由级入口(两个视图共用一个槽位,
    // 手机端 5 个槽位已满,刻意不加第 6 个「赛果」),它在两种视图下都该亮。
    const topNav = container.querySelector('nav[aria-label="主导航"]')!;
    const current = Array.from(topNav.querySelectorAll('a[aria-current="page"]')).map(
      (a) => a.textContent,
    );
    expect(current).toEqual([expectedActive]);
  });

  it("赛果入口指向 /matches?status=finished(不带 window —— 省略即该状态的默认窗口)", async () => {
    mockMeFetch({ authenticated: false });
    const { SiteNav: Nav } = await renderAt("/", "");
    const { container } = render(<Nav />);
    const link = Array.from(container.querySelectorAll("a")).find(
      (a) => a.textContent === "赛果",
    );
    expect(link?.getAttribute("href")).toBe("/matches?status=finished");
  });
});
