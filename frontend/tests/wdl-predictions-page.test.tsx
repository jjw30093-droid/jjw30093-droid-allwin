/**
 * legacy /league/[id]/wdl-predictions 页面测试(2026-08-16 权限口径修正)。
 *
 * 后端 /api/league/{id}/wdl-predictions 的响应已从三态(upcoming/live+locked/
 * live+unlocked)简化为两态(upcoming/live,不再有 locked 字段)。页面此前
 * 同时有"🔒 订阅解锁精确概率"(付费墙 UI)和"登录后免费查看完整三项概率"
 * (免费墙文案)两套互相矛盾的措辞——两者都必须删除,live 状态下始终展示
 * 完整概率分布。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import WdlPredictionsPage from "@/app/(member)/league/[id]/wdl-predictions/page";

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
];

function liveMatch(overrides: Record<string, unknown> = {}) {
  return {
    match_id: 1,
    date: "2026-08-20",
    round: "3",
    status: "NotStarted",
    home_team_id: 10,
    away_team_id: 20,
    home_team_name_zh: "主队",
    away_team_name_zh: "客队",
    days_until_kickoff: 2,
    availability: "live",
    tendency: "home",
    confidence: "normal",
    reason: "主场优势",
    p_home: 0.48,
    p_draw: 0.27,
    p_away: 0.25,
    ...overrides,
  };
}

function mockFetchByUrl(routes: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: unknown) => {
      const url = String(input);
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

describe("wdl-predictions 页面:live 状态恒展示完整概率,不再有付费墙/免费墙矛盾措辞", () => {
  it("live 且已有三项概率时,不出现'🔒 订阅解锁'或'登录后免费查看...概率'文案", async () => {
    mockFetchByUrl({
      "/api/v1/leagues": LEAGUES,
      "/api/league/47/wdl-predictions": {
        league_id: 47,
        season: "2025/2026",
        matches: [liveMatch()],
      },
    });

    const jsx = await WdlPredictionsPage({
      params: Promise.resolve({ id: "47" }),
      searchParams: Promise.resolve({}),
    });
    render(jsx);

    expect(screen.queryByText(/🔒/)).toBeNull();
    expect(screen.queryByText(/订阅解锁/)).toBeNull();
    expect(screen.queryByText(/登录后免费查看/)).toBeNull();
    // 完整三项概率必须真的渲染出来(不是只有 tendency 钩子)。
    expect(screen.getAllByText("48%").length).toBeGreaterThan(0);
    expect(screen.getAllByText("27%").length).toBeGreaterThan(0);
    expect(screen.getAllByText("25%").length).toBeGreaterThan(0);
  });
});
