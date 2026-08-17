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

describe("OddsTimeline 扁平 payload(审计 B2 的直接回归)", () => {
  it("渲染真实数值而非 —(current_odds 简化态:大数字块,取代原表格)", async () => {
    mockOddsResponse(fullTimelineBody([FLAT_SNAP]));
    render(<OddsTimeline matchId={1} />);
    await waitFor(() =>
      expect(screen.queryByText("Bet365", { selector: "span" })).not.toBeNull(),
    );
    expect(screen.getByText("0.99")).not.toBeNull();   // 主队水位
    expect(screen.getByText("1.25")).not.toBeNull();   // 盘口线
    expect(screen.getByText("0.91")).not.toBeNull();   // 客队水位
    expect(screen.queryByText("—")).toBeNull();
  });
});

describe("OddsTimeline 嵌套 payload,tier=full 且 display_mode=odds_changes(实时轮询形状,不得回归)", () => {
  it(
    "initial 与 latest 不同时,用箭头显示真实变化(2026-08-12 修复:此前只显示" +
      " latest 却打「初盘」标签,盘口真实变化从未展示)",
    async () => {
      mockOddsResponse(fullTimelineBody([NESTED_SNAP], { display_mode: "odds_changes" }));
      render(<OddsTimeline matchId={1} />);
      await waitFor(() => expect(screen.queryByText("有变动")).not.toBeNull());
      expect(screen.getByText("1.02 → 0.97")).not.toBeNull();
      expect(screen.getByText("0.88 → 0.93")).not.toBeNull();
      expect(screen.queryByText("—")).toBeNull();
    },
  );

  it('initial 与 latest 相同时,不画箭头(避免"1.25 → 1.25"这种噪音)', async () => {
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
    expect(screen.getAllByText("0.97").length).toBeGreaterThan(0);
    // "→" 会出现在数字块/表格之外的说明文案里,这里只需要确认数据单元格
    // 没有画箭头——用"有变动"标签的缺失来精确断言这一点。
    expect(screen.queryByText("有变动")).toBeNull();
  });
});

describe("OddsTimeline tier=full 但 display_mode=current_odds(样本不足以画走势,退化成简化态)", () => {
  it("大数字块显示 latest 值,不画箭头、不出表格(2026-08-14 重设计)", async () => {
    mockOddsResponse(fullTimelineBody([NESTED_SNAP]));
    render(<OddsTimeline matchId={1} />);
    await waitFor(() =>
      expect(screen.queryByText("Bet365", { selector: "span" })).not.toBeNull(),
    );
    expect(screen.getByText("0.97")).not.toBeNull();
    expect(screen.getByText("0.93")).not.toBeNull();
    expect(screen.queryByText("1.02 → 0.97")).toBeNull();
    expect(screen.queryByText("有变动")).toBeNull();
    expect(screen.getByText(/不是实时赔率/)).not.toBeNull();
    // 2026-08-16 权限口径修正:tier 恒为 "full"(MatchOddsAvailableDTO.tier
    // 已收窄成常量),不再存在"未登录只展示一条,登录查看完整时间线"这一档。
    expect(screen.queryByText(/未登录/)).toBeNull();
    expect(screen.queryByText(/免费登录查看完整时间线/)).toBeNull();
  });
});

describe("OddsTimeline 每家公司卡片展示各自的 observed_at(2026-08 审计:CompanyOddsRow.observedAt\n  已经从每条快照的真实 observed_at 正确填充,但 OddsNumbers() 渲染函数从未把它显示出来——\n  这里断言的是「每家公司自己的」时间,不是页面级共享一个新鲜度时间)", () => {
  it("两家公司(同一市场)observedAt 不同时,各自的北京时间文本都出现在渲染结果里", async () => {
    const companyA = {
      ...FLAT_SNAP,
      company_id: "8",
      company_name: "Bet365",
      observed_at: "2021-02-02T20:11:45Z",
    };
    const companyB = {
      ...FLAT_SNAP,
      company_id: "281",
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
    expect(timeA).not.toBe(timeB); // 两家公司的观测时间本就不同,不是同一个共享时间戳

    // 精确定位到各自公司的数字块(而不是页面上任意位置的时间文本——顶部
    // "这是 X 采集到的快照" 说明段用的是 freshestRow 单一时间戳,不能被
    // 误判成"每家公司都展示了"),断言 Bet365 那一块只含自己的时间、
    // Crown 那一块只含自己的时间。
    const companyABlock = screen.getByText("Bet365", { selector: "span" }).closest("div");
    const companyBBlock = screen.getByText("Crown", { selector: "span" }).closest("div");
    expect(companyABlock?.textContent).toContain(timeA);
    expect(companyBBlock?.textContent).toContain(timeB);
    expect(companyABlock?.textContent).not.toContain(timeB);
    expect(companyBBlock?.textContent).not.toContain(timeA);
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
