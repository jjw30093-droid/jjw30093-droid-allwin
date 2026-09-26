/**
 * 手机端体验第二批(2026-09-26)的导航行为:
 * - 底部导航 首页 / 比赛 / 联赛 / 精选 / 我的;「战绩」不再单独占入口;
 * - 「我的」未登录 → /login,已登录 → /account;
 * - /track-record 访问时「精选」(底部)与「每日精选」(桌面)保持高亮;
 * - 顶部栏没有黄色"登录"入口(CSS 在手机断点隐藏 .account,这里断言 DOM 结构仍在但由 CSS 控制;
 *   真正的显示/隐藏由 e2e 在真实浏览器里验证);
 * - 品牌副标题的联赛数量来自联赛配置,不写死;
 * - 比赛详情页顶栏有返回箭头:有来源(?from=)回来源,同站 referrer 回上一页,否则回 /matches。
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const nav = vi.hoisted(() => ({
  pathname: "/",
  push: vi.fn(),
  back: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: nav.push, back: nav.back }),
}));

import { BRAND_DESCRIPTOR, LEAGUE_COUNT, SiteNav, isMatchDetailPath } from "@/components/SiteNav";
import { LEAGUE_ZH } from "@/components/matches/zh";

function mockMe(body: unknown) {
  const headers = new Headers({ "content-type": "application/json" });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status: 200, headers })));
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }),
  );
}

beforeEach(() => {
  nav.pathname = "/";
  nav.push.mockClear();
  nav.back.mockClear();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const bottom = () => screen.getByTestId("mobile-bottom-nav");
const labels = () => within(bottom()).getAllByRole("link").map((a) => a.textContent);
const hrefOf = (name: string) => within(bottom()).getByRole("link", { name }).getAttribute("href");

describe("底部导航", () => {
  it("五项:首页 / 比赛 / 联赛 / 精选 / 我的,没有「战绩」", async () => {
    mockMe({ authenticated: false });
    render(<SiteNav />);
    expect(labels()).toEqual(["首页", "比赛", "联赛", "精选", "我的"]);
    expect(within(bottom()).queryByText("战绩")).toBeNull();
    expect(hrefOf("联赛")).toBe("/leagues");
    expect(hrefOf("精选")).toBe("/reco");
  });

  it("未登录:「我的」→ /login,文案仍是「我的」(不再写成「登录」)", async () => {
    mockMe({ authenticated: false });
    render(<SiteNav />);
    expect(hrefOf("我的")).toBe("/login");
    expect(within(bottom()).queryByText("登录")).toBeNull();
  });

  it("已登录:「我的」→ /account", async () => {
    mockMe({ authenticated: true, user: { id: "u1", display_name: "张三", role: "user" } });
    render(<SiteNav />);
    await waitFor(() => expect(hrefOf("我的")).toBe("/account"));
    expect(labels()).toEqual(["首页", "比赛", "联赛", "精选", "我的"]);
  });

  it.each([
    ["/", "首页"],
    ["/matches", "比赛"],
    ["/matches/5103641", "比赛"],
    ["/leagues", "联赛"],
    ["/league/47/standings", "联赛"],
    ["/reco", "精选"],
    ["/track-record", "精选"], // 战绩并入精选:访问旧路径时「精选」高亮
    ["/login", "我的"],
    ["/account", "我的"],
  ])("%s → 只有「%s」高亮", async (path, expected) => {
    nav.pathname = path;
    mockMe({ authenticated: false });
    render(<SiteNav />);
    const current = within(bottom())
      .getAllByRole("link")
      .filter((a) => a.getAttribute("aria-current") === "page")
      .map((a) => a.textContent);
    expect(current).toEqual([expected]);
  });
});

describe("桌面主导航", () => {
  it("没有「战绩」项;「每日精选」在 /track-record 下保持高亮", () => {
    nav.pathname = "/track-record";
    mockMe({ authenticated: false });
    const { container } = render(<SiteNav />);
    const top = container.querySelector('nav[aria-label="主导航"]')!;
    const texts = Array.from(top.querySelectorAll("a")).map((a) => a.textContent);
    expect(texts).not.toContain("战绩");
    expect(texts).toEqual(["首页", "比赛", "赛果", "联赛数据", "每日精选", "关于我们"]);
    const current = Array.from(top.querySelectorAll('a[aria-current="page"]')).map((a) => a.textContent);
    expect(current).toEqual(["每日精选"]);
  });
});

describe("品牌副标题", () => {
  it("联赛数量从联赛配置计算(LEAGUE_ZH),不写死", () => {
    expect(LEAGUE_COUNT).toBe(Object.keys(LEAGUE_ZH).length);
    expect(BRAND_DESCRIPTOR).toBe(`${Object.keys(LEAGUE_ZH).length} 个联赛的数据图表`);
    mockMe({ authenticated: false });
    render(<SiteNav />);
    expect(screen.getByText(BRAND_DESCRIPTOR)).toBeTruthy();
    expect(screen.queryByText("足球数据研究室")).toBeNull();
  });
});

describe("比赛详情页的顶栏返回箭头", () => {
  it("isMatchDetailPath:只在 /matches/{数字id} 下成立", () => {
    expect(isMatchDetailPath("/matches/5103641")).toBe(true);
    expect(isMatchDetailPath("/matches")).toBe(false);
    expect(isMatchDetailPath("/matches/abc")).toBe(false);
    expect(isMatchDetailPath("/league/47/matches")).toBe(false);
  });

  it("列表页/首页没有返回箭头", () => {
    for (const p of ["/", "/matches", "/leagues"]) {
      nav.pathname = p;
      mockMe({ authenticated: false });
      const { unmount } = render(<SiteNav />);
      expect(screen.queryByTestId("header-back")).toBeNull();
      unmount();
    }
  });

  function setLocation(search: string, referrer: string, historyLength: number) {
    Object.defineProperty(window, "location", {
      value: { ...window.location, search, origin: "http://localhost:3000" },
      configurable: true,
    });
    Object.defineProperty(document, "referrer", { value: referrer, configurable: true });
    Object.defineProperty(window.history, "length", { value: historyLength, configurable: true });
  }

  it("有来源页(?from= 是站内列表路径)→ 回来源页,保留筛选条件", () => {
    nav.pathname = "/matches/5103641";
    mockMe({ authenticated: false });
    setLocation("?from=%2Fmatches%3Fleague%3D47%26window%3D7d", "", 1);
    render(<SiteNav />);
    fireEvent.click(screen.getByTestId("header-back"));
    expect(nav.push).toHaveBeenCalledWith("/matches?league=47&window=7d");
    expect(nav.back).not.toHaveBeenCalled();
  });

  it("没有 from 但有同站 referrer 和浏览历史 → 回上一页", () => {
    nav.pathname = "/matches/5103641";
    mockMe({ authenticated: false });
    setLocation("", "http://localhost:3000/league/47/standings", 3);
    render(<SiteNav />);
    fireEvent.click(screen.getByTestId("header-back"));
    expect(nav.back).toHaveBeenCalled();
    expect(nav.push).not.toHaveBeenCalled();
  });

  it("没有来源页(直接打开/外站进来)→ 回 /matches", () => {
    nav.pathname = "/matches/5103641";
    mockMe({ authenticated: false });
    setLocation("", "https://example.com/somewhere", 1);
    render(<SiteNav />);
    fireEvent.click(screen.getByTestId("header-back"));
    expect(nav.push).toHaveBeenCalledWith("/matches");
    expect(nav.back).not.toHaveBeenCalled();
  });

  it("from 不是白名单内的站内路径(开放重定向企图)→ 忽略,按没有来源处理", () => {
    nav.pathname = "/matches/5103641";
    mockMe({ authenticated: false });
    setLocation("?from=%2F%2Fevil.example%2Fx", "", 1);
    render(<SiteNav />);
    fireEvent.click(screen.getByTestId("header-back"));
    expect(nav.push).toHaveBeenCalledWith("/matches");
    expect(nav.push).not.toHaveBeenCalledWith("//evil.example/x");
  });
});
