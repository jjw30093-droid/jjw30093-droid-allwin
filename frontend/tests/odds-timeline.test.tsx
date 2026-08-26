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
  buildCompanyHistory,
  listCompanies,
  pickChartCompany,
  buildMarketChart,
} from "@/components/matches/OddsTimeline";
import { MARKET_FIELDS } from "@/components/matches/zh";
import { flatOddsGroup } from "@/components/matches/types";
import { formatBeijingDateTime } from "@/components/matches/zh";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// EChart 依赖真实 DOM 尺寸,jsdom 下留桩即可。桩把 ariaSummary 原样渲染出来
// (真实组件也会把它放进 aria-label),这样测试能断言"图表画的是哪家公司/
// 哪个市场"而不用解析 ECharts option 内部结构。
vi.mock("@/components/EChart", () => ({
  EChart: ({ ariaSummary }: { ariaSummary: string }) => (
    <div data-testid="echart-stub">{ariaSummary}</div>
  ),
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

  it("buildCompanyHistory:最新在前,逐条与前一条比着色,首条(最早)为开盘 unknown", () => {
    const snap = (obs: string, home: number) => ({
      market: "1x2",
      company_id: "8",
      company_name: "Bet365",
      market_phase: "pre_match",
      source_updated_at: null,
      observed_at: obs,
      payload: { home, draw: 4, away: 6 },
    });
    // 故意乱序传入,函数内部按 observed_at 升序排后再比,再整体倒序。
    const snaps = [
      snap("2021-01-01T10:00:00Z", 1.5),
      snap("2021-01-01T12:00:00Z", 1.4), // 下调
      snap("2021-01-01T11:00:00Z", 1.5), // 相对 10:00 持平
    ] as unknown as Parameters<typeof buildCompanyHistory>[0];
    const hist = buildCompanyHistory(snaps, ["home"]);
    // 最新(12:00)在前
    expect(hist.map((e) => e.values?.home)).toEqual([1.4, 1.5, 1.5]);
    expect(hist[0].dirs.home).toBe("down"); // 12:00 vs 11:00 的 1.5 → 下调
    expect(hist[1].dirs.home).toBe("flat"); // 11:00 vs 10:00 的 1.5 → 持平
    expect(hist[2].dirs.home).toBe("unknown"); // 10:00 是开盘,无前一条
  });
});

describe("走势图纯函数(2026-08-26 P2:跟随选中公司 + 覆盖 ah/ou 市场)", () => {
  const snap = (market: string, companyId: string, companyName: string, obs: string, payload: unknown) => ({
    market,
    company_id: companyId,
    company_name: companyName,
    market_phase: "pre_match",
    source_updated_at: null,
    observed_at: obs,
    payload,
  });
  const COLORS = { axis: "#999", grid: "#eee", win: "#0a0", draw: "#888", loss: "#a00" };

  it("listCompanies:跨市场去重,按首次出现顺序返回 {id,label}", () => {
    const snaps = [
      snap("1x2", "8", "Bet365", "t1", { home: 1, draw: 2, away: 3 }),
      snap("ah", "3", "Crown", "t2", { home: 1, line: 1, away: 1 }),
      snap("ah", "8", "Bet365", "t3", { home: 1, line: 1, away: 1 }), // 同 id 再出现,不重复
    ] as unknown as Parameters<typeof listCompanies>[0];
    expect(listCompanies(snaps)).toEqual([
      { id: "8", label: "Bet365" },
      { id: "3", label: "Crown" },
    ]);
  });

  it("pickChartCompany:优先用 preferredId(展开的公司),样本不足或被隐藏时回落到可见样本最多的", () => {
    const raw = new Map([
      ["a", [1, 2, 3].map((n) => snap("1x2", "a", "A", `t${n}`, {}))], // 3 条
      ["b", [1, 2].map((n) => snap("1x2", "b", "B", `t${n}`, {}))], // 2 条
      ["c", [1].map((n) => snap("1x2", "c", "C", `t${n}`, {}))], // 只有 1 条,不够画线
    ]) as unknown as Parameters<typeof pickChartCompany>[0];

    // preferred 有效(样本≥2)→ 用它,即使不是样本最多的那家
    expect(pickChartCompany(raw, "b", new Set())).toBe("b");
    // preferred 样本不足(只有 1 条)→ 回落到样本最多且可见的
    expect(pickChartCompany(raw, "c", new Set())).toBe("a");
    // 没有 preferred → 回落到样本最多且可见的
    expect(pickChartCompany(raw, null, new Set())).toBe("a");
    // preferred 被隐藏 → 当作没有 preferred,回落
    expect(pickChartCompany(raw, "b", new Set(["b"]))).toBe("a");
    // 样本最多的那家也被隐藏 → 回落到下一个可见的
    expect(pickChartCompany(raw, null, new Set(["a"]))).toBe("b");
    // 全部隐藏 → 没有可画的
    expect(pickChartCompany(raw, null, new Set(["a", "b", "c"]))).toBeNull();
  });

  it("buildMarketChart:1x2 单 y 轴三条线;样本<2 时返回 null", () => {
    const snaps = [
      snap("1x2", "8", "Bet365", "2021-01-01T10:00:00Z", { home: 1.5, draw: 4, away: 6 }),
      snap("1x2", "8", "Bet365", "2021-01-01T11:00:00Z", { home: 1.4, draw: 4.2, away: 6.5 }),
    ] as unknown as Parameters<typeof buildMarketChart>[0];
    const chart = buildMarketChart(snaps, "1x2", "8", MARKET_FIELDS["1x2"], COLORS);
    expect(chart).not.toBeNull();
    expect(chart!.summary).toContain("Bet365");
    expect(chart!.summary).toContain("欧洲赔率");
    expect(chart!.option.series).toHaveLength(3);
    // 1x2 没有盘口线字段,不应该出现双 y 轴
    expect(Array.isArray(chart!.option.yAxis)).toBe(false);

    const tooFew = buildMarketChart(snaps.slice(0, 1), "1x2", "8", MARKET_FIELDS["1x2"], COLORS);
    expect(tooFew).toBeNull();
  });

  it("buildMarketChart:ah 市场盘口线字段拆到独立 y 轴(量纲与水位不同)", () => {
    const snaps = [
      snap("ah", "8", "Bet365", "2021-01-01T10:00:00Z", { home: 0.9, line: 1.0, away: 1.0 }),
      snap("ah", "8", "Bet365", "2021-01-01T11:00:00Z", { home: 0.85, line: 1.25, away: 1.05 }),
    ] as unknown as Parameters<typeof buildMarketChart>[0];
    const chart = buildMarketChart(snaps, "ah", "8", MARKET_FIELDS.ah, COLORS);
    expect(chart).not.toBeNull();
    expect(chart!.summary).toContain("水位为小数赔率,盘口线为球数门槛");
    expect(Array.isArray(chart!.option.yAxis)).toBe(true);
    expect((chart!.option.yAxis as unknown[]).length).toBe(2);
    // 盘口线(第 2 个字段,index 1)必须指到第二根 y 轴
    const lineSeries = (chart!.option.series as { name?: string; yAxisIndex?: number }[]).find(
      (s) => s.name === "盘口线",
    );
    expect(lineSeries?.yAxisIndex).toBe(1);
    const homeSeries = (chart!.option.series as { name?: string; yAxisIndex?: number }[]).find(
      (s) => s.name === "主队",
    );
    expect(homeSeries?.yAxisIndex ?? 0).toBe(0);
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

describe("OddsTimeline 点公司名下钻完整变化记录抽屉(P1)", () => {
  const seq = (obs: string, home: number) => ({
    market: "1x2",
    company_id: "8",
    company_name: "Bet365",
    market_phase: "pre_match",
    source_updated_at: null,
    observed_at: obs,
    payload: { home, draw: 4, away: 6 },
  });

  it("多条快照:行可点,点开出抽屉列出该公司变化记录;再点收起", async () => {
    mockOddsResponse(
      fullTimelineBody(
        [
          seq("2021-01-01T10:00:00Z", 1.5),
          seq("2021-01-01T11:00:00Z", 1.4),
          seq("2021-01-01T12:00:00Z", 1.3),
        ],
        { display_mode: "odds_changes" },
      ),
    );
    render(<OddsTimeline matchId={1} />);
    // 行是 role=button 且 aria-expanded 初始 false
    const row = await screen.findByRole("button", { name: /Bet365/ });
    expect(row.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("region", { name: /完整变化记录/ })).toBeNull();

    // 点开
    row.click();
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: /完整变化记录/ })).not.toBeNull(),
    );
    expect(row.getAttribute("aria-expanded")).toBe("true");
    // 抽屉里三条变化都在(1.50/1.40/1.30 各至少一次)
    const drawer = screen.getByRole("region", { name: /完整变化记录/ });
    expect(drawer.textContent).toContain("1.50");
    expect(drawer.textContent).toContain("1.40");
    expect(drawer.textContent).toContain("1.30");
    expect(drawer.textContent).toMatch(/共\s*3\s*次/);

    // 再点收起
    row.click();
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: /完整变化记录/ })).toBeNull(),
    );
  });

  it("只有一条快照:行不可点(没有变化记录可下钻)", async () => {
    mockOddsResponse(fullTimelineBody([seq("2021-01-01T10:00:00Z", 1.5)]));
    render(<OddsTimeline matchId={1} />);
    await waitFor(() =>
      expect(screen.queryByText("Bet365", { selector: "span" })).not.toBeNull(),
    );
    // 单条快照不给 role=button(无可展开内容)
    expect(screen.queryByRole("button", { name: /Bet365/ })).toBeNull();
  });
});

describe("OddsTimeline 走势图跟随选中公司 + 覆盖 ah/ou 市场(P2)", () => {
  const ahSnap = (companyId: string, companyName: string, obs: string, home: number) => ({
    market: "ah",
    company_id: companyId,
    company_name: companyName,
    market_phase: "pre_match",
    source_updated_at: null,
    observed_at: obs,
    payload: { home, line: 1.0, away: 1.0 },
  });

  it("ah 市场也画图(此前只有 1x2 有),默认跟样本最多的公司", async () => {
    mockOddsResponse(
      fullTimelineBody(
        [
          ahSnap("8", "Bet365", "2021-01-01T10:00:00Z", 0.9),
          ahSnap("8", "Bet365", "2021-01-01T11:00:00Z", 0.85),
        ],
        { display_mode: "odds_changes" },
      ),
    );
    render(<OddsTimeline matchId={1} />);
    const stub = await screen.findByTestId("echart-stub");
    expect(stub.textContent).toContain("Bet365");
    expect(stub.textContent).toContain("亚洲让球");
  });

  it("点开另一家公司的抽屉后,图表跟着切过去(与抽屉展示的是同一家)", async () => {
    mockOddsResponse(
      fullTimelineBody(
        [
          // Bet365 样本更多(3 条),默认应该是它
          ahSnap("8", "Bet365", "2021-01-01T10:00:00Z", 0.9),
          ahSnap("8", "Bet365", "2021-01-01T11:00:00Z", 0.85),
          ahSnap("8", "Bet365", "2021-01-01T12:00:00Z", 0.88),
          // Crown 样本较少(2 条)但足够画线
          ahSnap("3", "Crown", "2021-01-01T10:05:00Z", 0.92),
          ahSnap("3", "Crown", "2021-01-01T11:05:00Z", 0.9),
        ],
        { display_mode: "odds_changes" },
      ),
    );
    render(<OddsTimeline matchId={1} />);
    let stub = await screen.findByTestId("echart-stub");
    expect(stub.textContent).toContain("Bet365"); // 默认:样本最多

    const crownRow = screen.getByRole("button", { name: /Crown/ });
    crownRow.click();
    await waitFor(() => {
      stub = screen.getByTestId("echart-stub");
      expect(stub.textContent).toContain("Crown");
    });
    expect(stub.textContent).not.toContain("Bet365");
  });
});

describe("OddsTimeline 公司筛选(P2)", () => {
  const two = (obs: string, betHome: number, crownHome: number) => [
    {
      market: "1x2",
      company_id: "8",
      company_name: "Bet365",
      market_phase: "pre_match",
      source_updated_at: null,
      observed_at: obs,
      payload: { home: betHome, draw: 4, away: 6 },
    },
    {
      market: "1x2",
      company_id: "3",
      company_name: "Crown",
      market_phase: "pre_match",
      source_updated_at: null,
      observed_at: obs,
      payload: { home: crownHome, draw: 4, away: 6 },
    },
  ];

  it("勾掉一家公司:该公司行消失,摘要如实标注筛选口径,升降计数只算可见公司", async () => {
    mockOddsResponse(fullTimelineBody(two("2021-01-01T10:00:00Z", 1.5, 1.6)));
    render(<OddsTimeline matchId={1} />);
    await waitFor(() => expect(screen.queryByText("Bet365", { selector: "span" })).not.toBeNull());
    expect(screen.getByText("Crown", { selector: "span" })).not.toBeNull();
    expect(screen.getByText("2 家公司")).not.toBeNull();

    // 打开筛选面板,勾掉 Crown
    const summary = screen.getByText(/公司筛选/);
    summary.click();
    const crownCheckbox = screen.getByRole("checkbox", { name: "Crown" });
    crownCheckbox.click();

    await waitFor(() => expect(screen.queryByText("Crown", { selector: "span" })).toBeNull());
    expect(screen.getByText("Bet365", { selector: "span" })).not.toBeNull();
    // "1 家公司(共 2 家,已筛选)"——不能只报筛选后的数字
    expect(screen.getByText("1 家公司")).not.toBeNull();
    expect(screen.getByText(/共 2 家,已筛选/)).not.toBeNull();
  });

  it("被隐藏的公司不是它就是走势图的驱动者:隐藏后图表换到另一家可见公司", async () => {
    mockOddsResponse(
      fullTimelineBody(
        [...two("2021-01-01T10:00:00Z", 1.5, 1.6), ...two("2021-01-01T11:00:00Z", 1.4, 1.55)],
        { display_mode: "odds_changes" },
      ),
    );
    render(<OddsTimeline matchId={1} />);
    let stub = await screen.findByTestId("echart-stub");
    expect(stub.textContent).toContain("Bet365"); // 默认:插入顺序里第一家且样本数相同时排在前面

    screen.getByText(/公司筛选/).click();
    screen.getByRole("checkbox", { name: "Bet365" }).click();

    await waitFor(() => {
      stub = screen.getByTestId("echart-stub");
      expect(stub.textContent).toContain("Crown");
    });
    expect(stub.textContent).not.toContain("Bet365");
  });

  it("只有一家公司时不出筛选面板(筛选没有意义)", async () => {
    mockOddsResponse(fullTimelineBody([NESTED_SNAP]));
    render(<OddsTimeline matchId={1} />);
    await waitFor(() =>
      expect(screen.queryByText("Bet365", { selector: "span" })).not.toBeNull(),
    );
    expect(screen.queryByText(/公司筛选/)).toBeNull();
  });

  it(
    "真实生产回归(2026-08-26,match 3411527):同一显示名不同 company_id 只在" +
      "不同市场出现(id 8 实时/ah·ou vs id 281 历史/1x2,均归一显示为 Bet365)" +
      "时,筛选面板不跨市场汇总——否则会在同一份列表里出现两个互不联动、" +
      "无法区分的 Bet365 勾选项",
    async () => {
      const ahBet365 = {
        market: "ah",
        company_id: "8",
        company_name: "Bet365",
        market_phase: "pre_match",
        source_updated_at: null,
        observed_at: "2021-01-01T10:00:00Z",
        payload: { home: 0.9, line: 1.0, away: 1.0 },
      };
      const x1x2Bet365 = {
        market: "1x2",
        company_id: "281",
        company_name: "Bet365",
        market_phase: "pre_match",
        source_updated_at: null,
        observed_at: "2021-01-01T10:00:00Z",
        payload: { home: 1.5, draw: 4, away: 6 },
      };
      mockOddsResponse(fullTimelineBody([ahBet365, x1x2Bet365]));
      render(<OddsTimeline matchId={1} />);
      await waitFor(() => expect(screen.queryByRole("tab", { name: "亚洲让球" })).not.toBeNull());

      // 默认落在 1x2(MARKET_ORDER 里排最前),只有一家公司,不出筛选面板
      expect(screen.queryByText(/公司筛选/)).toBeNull();

      // 切到 ah:同样只有一家(不同 id 的那家),依然不该出现两个"Bet365"
      screen.getByRole("tab", { name: "亚洲让球" }).click();
      await waitFor(() => expect(screen.queryByText("0.90")).not.toBeNull());
      expect(screen.queryByText(/公司筛选/)).toBeNull();
      expect(screen.getAllByText("Bet365", { selector: "span" }).length).toBe(1);
    },
  );
});

describe("OddsTimeline 盘口线与水位的视觉区分(P2)", () => {
  it("ah 市场:盘口线列(表头+初盘+最新)带 data-kind=line,水位列不带", async () => {
    mockOddsResponse(fullTimelineBody([NESTED_SNAP], { display_mode: "odds_changes" }));
    const { container } = render(<OddsTimeline matchId={1} />);
    await waitFor(() =>
      expect(screen.queryByText("Bet365", { selector: "span" })).not.toBeNull(),
    );
    const lineCells = container.querySelectorAll('[data-kind="line"]');
    // 表头 1 + 初盘 1 + 最新 1 = 至少 3 个打了 data-kind="line" 的单元格
    expect(lineCells.length).toBeGreaterThanOrEqual(3);
    // 表头三列里,"盘口线" 那一列有 data-kind,"主队"/"客队" 没有
    const headerLine = screen.getByRole("columnheader", { name: "盘口线" });
    expect(headerLine.getAttribute("data-kind")).toBe("line");
    const headerHome = screen.getByRole("columnheader", { name: "主队" });
    expect(headerHome.getAttribute("data-kind")).toBeNull();
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
