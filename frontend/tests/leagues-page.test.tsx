/**
 * /leagues 页面测试。
 *
 * 2026-08-16 权限口径修正:LeagueInfo 不再有 entitlement/accessible/
 * requires_login 字段——所有联赛对匿名同等可访问,页面不得再区分
 * "当前可访问"/"登录后免费查看"/"需要登录"。
 *
 * 2026-09-13 按 FotMob「联赛」tab 重做为 48px 单行列表,新增三条回归:
 * ① 不再按拼音重排(顺序必须原样跟随后端,热度序由 LEAGUE_DISPLAY_ORDER 定);
 * ② 不再渲染「最近更新」——data_updated_at 结构上恒为 null(见
 *    routes_public.list_leagues 的两条分支),印出来只会是一句恒定的"暂无";
 * ③ AVAILABLE 不再挂「已有真实数据」badge——17/17 全是 AVAILABLE,恒真标签是噪声。
 *
 * ⚠️ 排版本身(行高 48px、一屏能看几个)jsdom 测不到(不跑 CSS),只能靠真实
 * 浏览器核对,见 CLAUDE.md §11.5。这里只守住 DOM 层面的结构与顺序。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import LeaguesPage from "@/app/leagues/page";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const LEAGUES = [
  {
    league_id: 47,
    code: "epl",
    name_zh: "英超",
    name_en: "Premier League",
    current_season: "2025/2026",
    available_seasons: ["2025/2026"],
    data_status: "AVAILABLE",
    data_updated_at: null,
  },
  {
    league_id: 223,
    code: "j1",
    name_zh: "日职联",
    name_en: "J1 League",
    current_season: "2026",
    available_seasons: ["2026"],
    data_status: "AVAILABLE",
    data_updated_at: null,
  },
];

function mockLeaguesFetch(payload: unknown = LEAGUES) {
  const headers = new Headers();
  headers.set("content-type", "application/json");
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), { status: 200, headers })),
  );
}

describe("/leagues", () => {
  it("渲染顺序原样跟随后端,不再按拼音重排", async () => {
    // 故意给一个"拼音序会把它排到前面"的联赛(澳超 A<Y)放在后端返回的末尾:
    // 旧实现 priority()||localeCompare 会把它顶到英超前面,新实现必须原样保序。
    const withAleague = [...LEAGUES, {
      league_id: 113, code: "aleague", name_zh: "澳超", name_en: "A-League",
      current_season: "2026/2027", available_seasons: ["2026/2027"],
      data_status: "AVAILABLE", data_updated_at: null,
    }];
    mockLeaguesFetch(withAleague);
    const { container } = render(await LeaguesPage());
    const names = [...container.querySelectorAll("a[href^='/league/']")].map(
      (a) => a.textContent?.replace(/\d{4}.*$/, "").trim(),
    );
    expect(names).toEqual(["英超", "日职联", "澳超"]);
  });

  it("每一行整行可点,进的是该联赛的 overview", async () => {
    mockLeaguesFetch();
    const { container } = render(await LeaguesPage());
    const hrefs = [...container.querySelectorAll("a[href^='/league/']")].map((a) =>
      a.getAttribute("href"),
    );
    expect(hrefs).toEqual(["/league/47/overview", "/league/223/overview"]);
  });

  it("不再渲染「最近更新」与恒真的「已有真实数据」badge", async () => {
    mockLeaguesFetch();
    render(await LeaguesPage());
    expect(screen.queryByText(/暂无可信时间/)).toBeNull();
    expect(screen.queryByText(/最近更新/)).toBeNull();
    expect(screen.queryByText("已有真实数据")).toBeNull();
  });

  it("不出现'登录后免费查看'/'需要登录'/'当前可访问'这类访问权限区分文案", async () => {
    mockLeaguesFetch();
    const jsx = await LeaguesPage();
    render(jsx);

    expect(screen.getByText("英超")).not.toBeNull();
    expect(screen.getByText("日职联")).not.toBeNull();
    expect(screen.queryByText("登录后免费查看")).toBeNull();
    expect(screen.queryByText("需要登录")).toBeNull();
    expect(screen.queryByText("当前可访问")).toBeNull();
    expect(screen.queryByText("访问权限")).toBeNull();
  });
});
