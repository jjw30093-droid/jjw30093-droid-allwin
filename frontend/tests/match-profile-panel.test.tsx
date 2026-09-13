/**
 * MatchProfilePanel:「数据 → 风格」百分位模块的两个口径切换器(2026-09-13)。
 *
 * 这里守的全是 DOM/取数行为——**排版本身 jsdom 测不到**(不跑 CSS),
 * 44px 触控目标、窄屏是否换行、选中态配色只能靠真实浏览器与
 * percentile-contrast.test.ts 兜(CLAUDE.md §11.5)。
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    expect(screen.getByText("进攻百分位")).toBeTruthy();
    // 首屏的 data_profile 已经内嵌在 /preview 里,再请求一次是纯浪费
    await waitFor(() => expect(fn).not.toHaveBeenCalled());
  });

  it("切换后按选中口径取子资源,不重跑整条 /preview", async () => {
    const fn = mockFetch({ match_id: 123, profile: profile({ window_n: 3, home_matches: 3, away_matches: 3 }) });
    render(panel());
    fireEvent.click(screen.getByRole("tab", { name: "近 3 场" }));

    await waitFor(() => expect(fn).toHaveBeenCalledTimes(1));
    expect(String(fn.mock.calls[0][0])).toContain(
      "/api/v1/matches/123/data-profile?venue=same_venue&n=3",
    );
    // 窗口说明三段各印一行,所以是 getAllByText——数字必须真的跟着切换变
    await waitFor(() => expect(screen.getAllByText(/近 3 个主场/).length).toBe(3));
  });

  it("切回看过的组合零请求(缓存命中)", async () => {
    const fn = mockFetch({ match_id: 123, profile: profile({ window_n: 5 }) });
    render(panel());
    fireEvent.click(screen.getByRole("tab", { name: "近 5 场" }));
    await waitFor(() => expect(fn).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("tab", { name: "近 10 场" }));
    fireEvent.click(screen.getByRole("tab", { name: "近 5 场" }));
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("★ 取数中旧内容仍在 DOM,不塌成空白", async () => {
    // 请求只有 ~130ms,把 ~1000px 高的四块卸载掉会让页面剧烈跳动,
    // 代价远大于收益——取数中只压一层遮罩 + aria-busy。
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));
    const { container } = render(panel());
    fireEvent.click(screen.getByRole("tab", { name: "全部" }));

    expect(screen.getByText("进攻百分位")).toBeTruthy();
    expect(screen.getByText("防守百分位")).toBeTruthy();
    expect(screen.getByText("控球百分位")).toBeTruthy();
    expect(container.querySelector("[aria-busy='true']")).not.toBeNull();
  });

  it("取数失败:旧内容不清空,给一条可重试的提示", async () => {
    const fn = vi.fn().mockRejectedValue(new Error("boom"));
    vi.stubGlobal("fetch", fn);
    render(panel());
    fireEvent.click(screen.getByRole("tab", { name: "全部" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "重试" })).toBeTruthy());
    expect(screen.getByText("进攻百分位")).toBeTruthy(); // 没白屏

    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(fn.mock.calls.length).toBeGreaterThan(1));
  });

  it("★ 空态下切换器仍然在屏,用户不会被锁死在白页上", () => {
    mockFetch({});
    render(panel(profile({
      home_available: false, away_available: false, groups: [], highlights: [],
      unavailable_reason: "两队本赛季比赛场次都不足,暂无法给出联赛百分位画像。",
      venue_mode: "all",
    })));
    expect(screen.getByRole("tab", { name: "相同主客场" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "近 10 场" })).toBeTruthy();
    // 空态文案用后端给的那句(已按口径分支),不是前端写死的"同主客场"
    expect(screen.getByText(/两队本赛季比赛场次都不足/)).toBeTruthy();
    expect(screen.queryByText(/同主客场比赛都不足/)).toBeNull();
  });

  it("★ 近 3 场不得把三块整体打空(后端 min_n 联动的前端孪生)", () => {
    mockFetch({});
    render(panel(profile({ window_n: 3, home_matches: 3, away_matches: 3, venue_mode: "all" })));
    expect(screen.getByText("进攻百分位")).toBeTruthy();
    expect(screen.getByText("防守百分位")).toBeTruthy();
    expect(screen.getByText("控球百分位")).toBeTruthy();
  });

  it("两组切换器各有可读的 aria-label,选中项 aria-selected=true", () => {
    mockFetch({});
    render(panel());
    expect(screen.getByRole("tablist", { name: "统计范围" })).toBeTruthy();
    expect(screen.getByRole("tablist", { name: "样本场次" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "相同主客场" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tab", { name: "全部" }).getAttribute("aria-selected")).toBe("false");
  });
});
