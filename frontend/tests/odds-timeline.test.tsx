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
import {
  OddsTimeline,
  oddsDelta,
  formatDelta,
  summarizeMarketMovement,
} from "@/components/matches/OddsTimeline";
import { flatOddsGroup } from "@/components/matches/types";
import { formatBeijingDateTime } from "@/components/matches/zh";

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

describe("涨跌纯函数(2026-08-26 赔率展示重做)", () => {
  it("oddsDelta:与初盘比,量化到 2 位小数;up/down/flat/unknown 四态", () => {
    expect(oddsDelta(1.02, 0.97)).toEqual({ dir: "down", delta: -0.05 });
    expect(oddsDelta(1.0, 1.25)).toEqual({ dir: "up", delta: 0.25 });
    expect(oddsDelta(0.97, 0.97)).toEqual({ dir: "flat", delta: 0 });
    // "没有初盘可比"(单条快照)必须是 unknown,不能被当成 flat——两者语义不同。
    expect(oddsDelta(null, 0.97)).toEqual({ dir: "unknown", delta: 0 });
    expect(oddsDelta(0.97, null)).toEqual({ dir: "unknown", delta: 0 });
    // IEEE754 噪声吸收(与 cleanOddsNum 同一处理):1.93 的 ULP 抖动不产生假变化。
    expect(oddsDelta(1.9300000000000002, 1.93)).toEqual({ dir: "flat", delta: 0 });
  });

  it("formatDelta:带符号两位小数,负号用真减号 U+2212", () => {
    expect(formatDelta(0.25)).toBe("+0.25");
    expect(formatDelta(-0.05)).toBe("−0.05"); // U+2212,不是 ASCII '-'
    expect(formatDelta(-0.05).charCodeAt(0)).toBe(0x2212);
  });

  it("summarizeMarketMovement:按代表字段逐公司计升/平/降/无初盘 家数", () => {
    const rows = [
      { initial: { home: 1.0 }, current: { home: 1.2 } }, // up
      { initial: { home: 2.0 }, current: { home: 1.8 } }, // down
      { initial: { home: 1.5 }, current: { home: 1.5 } }, // flat
      { initial: null, current: { home: 1.9 } }, // unknown(只有一条快照)
    ] as unknown as Parameters<typeof summarizeMarketMovement>[0];
    expect(summarizeMarketMovement(rows, "home")).toEqual({
      up: 1,
      down: 1,
      flat: 1,
      unknown: 1,
      total: 4,
    });
  });
});

describe("OddsTimeline 市场切换 + 每公司行(结构重做)", () => {
  it("多市场时出 tab,默认选第一个,点击切换只渲染选中市场", async () => {
    const one = (market: string, over: number) => ({
      market,
      company_id: "8",
      company_name: "Bet365",
      market_phase: "pre_match",
      source_updated_at: null,
      observed_at: "2021-02-02T20:11:45Z",
      payload:
        market === "1x2"
          ? { home: 1.5, draw: 4.0, away: 6.0 }
          : { over, line: 2.5, under: 0.9 },
    });
    mockOddsResponse(fullTimelineBody([one("1x2", 0), one("ou", 0.95)]));
    render(<OddsTimeline matchId={1} />);
    await waitFor(() => expect(screen.queryByRole("tab", { name: "胜平负(欧赔)" })).not.toBeNull());
    // 两个市场两个 tab,默认 1x2 选中
    expect(screen.getByRole("tab", { name: "胜平负(欧赔)" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tab", { name: "大小球" }).getAttribute("aria-selected")).toBe("false");
    // 默认渲染 1x2:主胜 1.50 在,大小球的 0.95 不在
    expect(screen.getByText("1.50")).not.toBeNull();
    expect(screen.queryByText("0.95")).toBeNull();
    // 切到大小球
    screen.getByRole("tab", { name: "大小球" }).click();
    await waitFor(() => expect(screen.queryByText("0.95")).not.toBeNull());
    expect(screen.queryByText("1.50")).toBeNull(); // 1x2 不再渲染
  });
});

describe("OddsTimeline 嵌套 payload(初盘≠最新):两行叠放 + 方向 + 幅度", () => {
  it("初盘行给初值,最新行给现值并标 ↑/↓ 幅度与方向色", async () => {
    mockOddsResponse(fullTimelineBody([NESTED_SNAP], { display_mode: "odds_changes" }));
    render(<OddsTimeline matchId={1} />);
    await waitFor(() =>
      expect(screen.queryByText("Bet365", { selector: "span" })).not.toBeNull(),
    );
    // 初盘行(--ink-3 参考值)
    expect(screen.getByText("1.02")).not.toBeNull();
    expect(screen.getByText("0.88")).not.toBeNull();
    // 最新行现值
    expect(screen.getByText("0.97")).not.toBeNull(); // home 下调
    expect(screen.getByText("0.93")).not.toBeNull(); // away 上调
    // 幅度 + 方向箭头(home 1.02→0.97=−0.05 下;line 1.00→1.25=+0.25 上;away +0.05 上)
    expect(screen.getByText("↓0.05")).not.toBeNull();
    expect(screen.getByText("↑0.25")).not.toBeNull();
    expect(screen.getByText("↑0.05")).not.toBeNull();
    // 方向色由单元格 data-dir 承载(青绿=up / 板岩蓝=down,§11.2 安全)
    const downCell = screen.getByText("↓0.05").closest("[data-dir]");
    expect(downCell?.getAttribute("data-dir")).toBe("down");
    const upCell = screen.getByText("↑0.25").closest("[data-dir]");
    expect(upCell?.getAttribute("data-dir")).toBe("up");
    // 旧的"挤在一格"写法不得回归
    expect(screen.queryByText("1.02 → 0.97")).toBeNull();
  });

  it("初盘==最新时:两行都在,方向为持平(—),不产生假箭头", async () => {
    const stable = {
      ...NESTED_SNAP,
      payload: {
        initial: { home: 0.97, line: 1.25, away: 0.93 },
        latest: { home: 0.97, line: 1.25, away: 0.93 },
      },
    };
    mockOddsResponse(fullTimelineBody([stable], { display_mode: "odds_changes" }));
    render(<OddsTimeline matchId={1} />);
    await waitFor(() =>
      expect(screen.queryByText("Bet365", { selector: "span" })).not.toBeNull(),
    );
    // 初盘 + 最新两行,同值各出现一次
    expect(screen.getAllByText("0.97").length).toBe(2);
    // 持平用 "—",不画 ↑/↓
    expect(screen.queryByText(/↑/)).toBeNull();
    expect(screen.queryByText(/↓/)).toBeNull();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });
});

describe("OddsTimeline display_mode=current_odds(观测点不足):仍展示可得的初/最新,并如实标注非实时", () => {
  it("单条含内嵌初盘的快照:两行照出,同时挂'不是实时刷新'提示", async () => {
    mockOddsResponse(fullTimelineBody([NESTED_SNAP]));
    render(<OddsTimeline matchId={1} />);
    await waitFor(() =>
      expect(screen.queryByText("Bet365", { selector: "span" })).not.toBeNull(),
    );
    expect(screen.getByText("1.02")).not.toBeNull(); // 内嵌初盘照样展示,不再被藏
    expect(screen.getByText("0.97")).not.toBeNull();
    expect(screen.getByText(/不是实时刷新/)).not.toBeNull();
    expect(screen.queryByText(/未登录/)).toBeNull();
  });

  it("扁平单条快照(无内嵌初盘):退化成单行现值,不画方向,不假装有初盘", async () => {
    mockOddsResponse(fullTimelineBody([FLAT_SNAP]));
    render(<OddsTimeline matchId={1} />);
    await waitFor(() =>
      expect(screen.queryByText("Bet365", { selector: "span" })).not.toBeNull(),
    );
    expect(screen.getByText("0.99")).not.toBeNull();
    expect(screen.getByText("1.25")).not.toBeNull();
    expect(screen.getByText("0.91")).not.toBeNull();
    expect(screen.queryByText("初盘")).toBeNull(); // 没有初盘就不显示初盘行
    expect(screen.queryByText(/↑|↓/)).toBeNull();
  });
});

describe("OddsTimeline 每家公司展示各自的 observed_at(2026-08 审计:每公司自己的观测时间,不是页面级共享时间)", () => {
  it("两家公司(同一市场)observedAt 不同时,各自时间落在各自那一行", async () => {
    const companyA = {
      ...FLAT_SNAP,
      company_id: "8",
      company_name: "Bet365",
      observed_at: "2021-02-02T20:11:45Z",
    };
    const companyB = {
      ...FLAT_SNAP,
      company_id: "3",
      company_name: "Crown",
      observed_at: "2021-03-15T09:30:00Z",
    };
    mockOddsResponse(fullTimelineBody([companyA, companyB]));
    render(<OddsTimeline matchId={1} />);
    await waitFor(() =>
      expect(screen.queryByText("Bet365", { selector: "span" })).not.toBeNull(),
    );
    expect(screen.getByText("Crown", { selector: "span" })).not.toBeNull();

    const timeA = formatBeijingDateTime(companyA.observed_at);
    const timeB = formatBeijingDateTime(companyB.observed_at);
    expect(timeA).not.toBeNull();
    expect(timeB).not.toBeNull();
    expect(timeA).not.toBe(timeB); // 两家公司观测时间本就不同,不是共享时间戳

    // .coName 容器(公司名 span 的父 div)里,各含自己的时间、不含对方的。
    const blockA = screen.getByText("Bet365", { selector: "span" }).closest("div");
    const blockB = screen.getByText("Crown", { selector: "span" }).closest("div");
    expect(blockA?.textContent).toContain(timeA);
    expect(blockB?.textContent).toContain(timeB);
    expect(blockA?.textContent).not.toContain(timeB);
    expect(blockB?.textContent).not.toContain(timeA);
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

  it(
    "赔率数值带浮点 ULP 噪声时,显示成干净的两位小数(真实用户报告" +
      " 2026-08-21:match 5125184 的 ah 市场 initial home_or_over 显示成" +
      ' "1.9300000000000002")',
    async () => {
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
            market: "ah", period: "initial", source: "football_uk_jka", provider: "Bet365",
            line: -0.25, home_or_over: 1.9300000000000002, draw: null, away_or_under: 1.88,
          },
        ],
        note: "本场为历史存档赔率,仅有初盘与临场两个观测点,无完整走势时间线。",
      });
      render(<OddsTimeline matchId={1} />);
      await waitFor(() => expect(screen.queryByText(/历史存档赔率/)).not.toBeNull());
      expect(screen.getByText("1.93")).not.toBeNull();
      expect(screen.queryByText(/1\.9300000000000002/)).toBeNull();
    },
  );

  it(
    "同一公司出现在两个存档批次里、数值不同时,标出批次来源" +
      "(2026-08-12 修复:此前只显示 provider,两行「Bet365 / 临场」" +
      "看起来像未解释的重复,实际是港赔/欧赔两种格式的独立抓取)",
    async () => {
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
            market: "1x2", period: "latest", source: "asset_a_json", provider: "Bet365",
            line: null, home_or_over: 1.91, draw: 3.6, away_or_under: 3.9,
          },
          {
            market: "1x2", period: "latest", source: "asset_b_footballdata", provider: "Bet365",
            line: null, home_or_over: 1.91, draw: 3.6, away_or_under: 3.9,
          },
        ],
        note: "本场为历史存档赔率,仅有初盘与临场两个观测点,无完整走势时间线。",
      });
      render(<OddsTimeline matchId={1} />);
      await waitFor(() => expect(screen.queryAllByText("Bet365").length).toBe(2));
      expect(screen.getByText("存档 A")).not.toBeNull();
      expect(screen.getByText("存档 B·football-data")).not.toBeNull();
    },
  );
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
