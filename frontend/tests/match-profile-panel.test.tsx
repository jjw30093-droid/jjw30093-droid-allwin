/**
 * MatchProfilePanel:「数据 → 风格」排名模块(2026-10-02 起没有口径切换器与口径说明)。
 *
 * 这里守的全是 DOM/取数行为——**排版本身 jsdom 测不到**(不跑 CSS),
 * 44px 触控目标、窄屏是否换行、选中态配色只能靠真实浏览器与
 * percentile-contrast.test.ts 兜(CLAUDE.md §11.5)。
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MatchProfilePanel } from "@/components/matches/MatchProfilePanel";
import type { DataProfile } from "@/components/matches/matchProfile";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function metric(overrides: Partial<DataProfile["groups"][number]["metrics"][number]> = {}) {
  return {
    key: "xg", name_zh: "预期进球(xG)", unit: "球/场", direction: "higher_better",
    semantic: "performance", home_value: 1.9, away_value: 1.1,
    home_percentile: 87, away_percentile: 34,
    home_complete: true, away_complete: true, league_sample_size: 18,
    ...overrides,
  };
}

function profile(overrides: Partial<DataProfile> = {}): DataProfile {
  return {
    home_matches: 10, away_matches: 10, home_available: true, away_available: true,
    groups: [
      { key: "attack", title_zh: "攻", metrics: [metric()], home_group_percentile: 87, away_group_percentile: 34, home_peers: [], away_peers: [] },
      { key: "defence", title_zh: "守", metrics: [metric({ key: "xga", name_zh: "被创造 xG", direction: "lower_better" })], home_group_percentile: 55, away_group_percentile: 40, home_peers: [], away_peers: [] },
      { key: "control", title_zh: "控", metrics: [metric({ key: "possession", name_zh: "控球率", unit: "%", semantic: "style" })], home_group_percentile: 60, away_group_percentile: 50, home_peers: [], away_peers: [] },
    ],
    highlights: [], unavailable_reason: null,
    comparison_mode: "league_percentile", scope_note: null,
    venue_mode: "same_venue", window_n: 10,
    ...overrides,
  };
}

function mockFetch(body: unknown) {
  const headers = new Headers();
  headers.set("content-type", "application/json");
  const fn = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status: 200, headers }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

function panel(p: DataProfile = profile()) {
  return (
    <MatchProfilePanel
      matchId={123}
      homeName="利物浦"
      awayName="布伦特福德"
      initialProfile={p}
    />
  );
}

describe("MatchProfilePanel", () => {
  it("默认口径直接用首屏那份,一次请求都不发", async () => {
    const fn = mockFetch({});
    render(panel());
    expect(screen.getByText("进攻排名")).toBeTruthy();
    // 首屏的 data_profile 已经内嵌在 /preview 里,再请求一次是纯浪费
    await waitFor(() => expect(fn).not.toHaveBeenCalled());
  });

  it("空态文案用后端给的那句,不是前端写死的", () => {
    mockFetch({});
    render(panel(profile({
      home_available: false, away_available: false, groups: [], highlights: [],
      unavailable_reason: "两队近期比赛数据不足。",
      venue_mode: "recent",
    })));
    expect(screen.getByText("两队近期比赛数据不足。")).toBeTruthy();
  });

  it("★ 近 3 场不得把三块整体打空(后端 min_n 联动的前端孪生)", () => {
    mockFetch({});
    render(panel(profile({ window_n: 3, home_matches: 3, away_matches: 3, venue_mode: "all" })));
    expect(screen.getByText("进攻排名")).toBeTruthy();
    expect(screen.getByText("防守排名")).toBeTruthy();
    expect(screen.getByText("控球排名")).toBeTruthy();
  });

  it("没有口径切换器、口径说明和样本行(站长 2026-10-02:口径细节没人看)", () => {
    mockFetch({});
    const { container } = render(panel(profile({ venue_mode: "recent" })));
    expect(screen.queryByRole("tablist", { name: "统计范围" })).toBeNull();
    expect(screen.queryByRole("tablist", { name: "样本场次" })).toBeNull();
    expect(screen.queryByText(/相同主客场|近 \d+ 场|联赛样本|按联赛主场数据排名|不是本场预测/)).toBeNull();
    expect(container.querySelector('[class*="footNote"]')).toBeNull();
  });
});
