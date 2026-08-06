/**
 * OddsTimeline 渲染测试(2026-08-06 审计 B2:payload 双形状导致 Premium
 * 赔率表整片 "—",且此前该组件零测试覆盖——本文件即回归护栏)。
 *
 * 关键断言:
 * - 扁平 payload(历史回填,库内 73.5 万行)渲染出真实数值,不是 "—";
 * - 嵌套 payload(实时轮询)同样渲染数值(取 latest);
 * - open_close_only(两点摘要)只出表格,绝不渲染走势图(§6.2);
 * - available=false 渲染 reason 文案。
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OddsTimeline } from "@/components/matches/OddsTimeline";
import { flatOddsGroup } from "@/components/matches/types";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// EChart 依赖真实 DOM 尺寸,jsdom 下留桩即可;测试关心"是否渲染了图表容器"
vi.mock("@/components/EChart", () => ({
  EChart: () => <div data-testid="echart-stub" />,
}));

function mockOddsResponse(body: unknown) {
  const headers = new Headers();
  headers.set("content-type", "application/json");
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(body), { status: 200, headers }),
    ),
  );
}

const FLAT_SNAP = {
  market: "ah",
  company_id: "8",
  company_name: "Bet365",
  market_phase: "pre_match",
  source_updated_at: null,
  observed_at: "2021-02-02T20:11:45Z",
  payload: { home: 0.99, line: 1.25, away: 0.91 },
};

const NESTED_SNAP = {
  market: "ah",
  company_id: "8",
  company_name: "Bet365",
  market_phase: "pre_match",
  source_updated_at: null,
  observed_at: "2021-02-02T20:11:45Z",
  payload: { initial: { home: 1.02, line: 1.0, away: 0.88 }, latest: { home: 0.97, line: 1.25, away: 0.93 } },
};

function fullTimelineBody(snapshots: unknown[], overrides: Record<string, unknown> = {}) {
  return {
    match_id: 1,
    available: true,
    tier: "full",
    coverage_tier: "full_timeline",
    home_away_inverted: false,
    observation_count: snapshots.length,
    display_mode: "current_odds",
    snapshots,
    ...overrides,
  };
}

describe("flatOddsGroup 归一(与后端 normalize_odds_payload 同规则)", () => {
  it("扁平原样返回", () => {
    expect(flatOddsGroup({ home: 0.86, line: 2.5, away: 1.04 })).toEqual({
      home: 0.86,
      line: 2.5,
      away: 1.04,
    });
  });
  it("嵌套取 latest,latest 空则退 initial", () => {
    expect(flatOddsGroup({ initial: { home: 1 }, latest: { home: 2 } })).toEqual({ home: 2 });
    expect(flatOddsGroup({ initial: { home: 1 }, latest: null })).toEqual({ home: 1 });
    expect(flatOddsGroup({ initial: null, latest: null })).toBeNull();
  });
});

describe("OddsTimeline 扁平 payload(审计 B2 的直接回归)", () => {
  it("渲染真实数值而非 —", async () => {
    mockOddsResponse(fullTimelineBody([FLAT_SNAP]));
    render(<OddsTimeline matchId={1} />);
    await waitFor(() => expect(screen.queryByText("Bet365")).not.toBeNull());
    expect(screen.getByText("0.99")).not.toBeNull();   // 主队水位
    expect(screen.getByText("1.25")).not.toBeNull();   // 盘口线
    expect(screen.getByText("0.91")).not.toBeNull();   // 客队水位
    expect(screen.queryByText("—")).toBeNull();
  });
});

describe("OddsTimeline 嵌套 payload(实时轮询形状,不得回归)", () => {
  it("取 latest 组渲染数值", async () => {
    mockOddsResponse(fullTimelineBody([NESTED_SNAP]));
    render(<OddsTimeline matchId={1} />);
    await waitFor(() => expect(screen.queryByText("Bet365")).not.toBeNull());
    expect(screen.getByText("0.97")).not.toBeNull();
    expect(screen.getByText("0.93")).not.toBeNull();
    expect(screen.queryByText("—")).toBeNull();
  });
});

describe("OddsTimeline open_close_only(§6.2:两点摘要绝不画走势图)", () => {
  it("只出表格,无图表,无系统检测时间列", async () => {
    mockOddsResponse({
      match_id: 1,
      available: true,
      tier: "full",
      coverage_tier: "open_close_only",
      home_away_inverted: false,
      observation_count: 0,
      display_mode: "current_odds",
      snapshots: [],
      summary_points: [
        {
          market: "1x2", period: "initial", source: "asset_a_json", provider: "Bet365",
          line: null, home_or_over: 1.5, draw: 4.0, away_or_under: 6.0,
        },
        {
          market: "1x2", period: "latest", source: "asset_a_json", provider: "Bet365",
          line: null, home_or_over: 1.44, draw: 4.75, away_or_under: 6.5,
        },
      ],
      note: "本场为历史存档赔率,仅有初盘与临场两个观测点,无完整走势时间线。",
    });
    render(<OddsTimeline matchId={1} />);
    await waitFor(() => expect(screen.queryByText(/历史存档赔率/)).not.toBeNull());
    expect(screen.getByText("初盘")).not.toBeNull();
    expect(screen.getByText("临场")).not.toBeNull();
    expect(screen.getByText("1.44")).not.toBeNull();
    expect(screen.queryByTestId("echart-stub")).toBeNull();       // 绝不画图
    expect(screen.queryByText("系统检测时间")).toBeNull();          // 无时间戳列
  });
});

describe("OddsTimeline 不可用态", () => {
  it("渲染后端 reason", async () => {
    mockOddsResponse({ match_id: 1, available: false, reason: "该场比赛暂无已验证的赔率数据映射" });
    render(<OddsTimeline matchId={1} />);
    await waitFor(() =>
      expect(screen.queryByText("该场比赛暂无已验证的赔率数据映射")).not.toBeNull(),
    );
  });
});
