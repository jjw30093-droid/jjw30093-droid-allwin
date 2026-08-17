/**
 * /leagues 页面测试(2026-08-16 权限口径修正)。
 *
 * LeagueInfo 不再有 entitlement/accessible/requires_login 字段——所有联赛
 * 对匿名同等可访问,页面不得再区分"当前可访问"/"登录后免费查看"/"需要登录"。
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

function mockLeaguesFetch() {
  const headers = new Headers();
  headers.set("content-type", "application/json");
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response(JSON.stringify(LEAGUES), { status: 200, headers })),
  );
}

describe("/leagues:不再区分联赛的登录门禁状态", () => {
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
