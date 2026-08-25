/**
 * 比赛详情页板块顺序(2026-08-25 对齐 FotMob,站长手机比对拍板):
 * - 已完赛「总览」纵向:势头图 → 重点数据 → 最高评分 → 关键事件 → 比赛信息
 *   → 裁判 → 数据倾向(其后既有组);
 * - 「射门」tab:射门落点最顶,势头图不再出现(整块挪总览,不重复)。
 *
 * 重取数/重图表的子组件按名 mock 成占位 div——本文件测的是**编排顺序**,
 * 不是那些组件自身(各有专门测试)。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { MatchDetailResponse, MatchReportResponse } from "@/lib/api-v1";

vi.mock("@/components/matches/OddsTimeline", () => ({
  OddsTimeline: () => <div data-testid="mock-odds-timeline" />,
}));
vi.mock("@/components/matches/CooccurrenceSection", () => ({
  CooccurrenceSection: () => <div data-testid="mock-cooccurrence" />,
}));
vi.mock("@/components/matches/MarketCardsSection", () => ({
  MarketCardsSection: () => <div data-testid="mock-market-cards" />,
}));
vi.mock("@/components/matches/MomentumChart", () => ({
  MomentumChart: () => <div data-testid="mock-momentum-chart" />,
}));
vi.mock("@/components/matches/MatchShotsSection", () => ({
  MatchShotsSection: () => <div data-testid="mock-shots-section" />,
}));

import { MatchDetailBody } from "@/components/matches/MatchDetailBody";

afterEach(cleanup);

type MatchReport = Extract<MatchReportResponse, { available: true }>;

function detailFixture(): MatchDetailResponse {
  return {
    match: {
      match_id: 9002,
      league_id: 47,
      season: "2026/2027",
      date_utc: "2026-08-20",
      kickoff_at_utc: "2026-08-20T19:00:00Z",
      status: "Finish",
      home: { team_id: 1001, name: "主队", name_en: "Home", crest_url: null },
      away: { team_id: 1002, name: "客队", name_en: "Away", crest_url: null },
      home_score: 2,
      away_score: 1,
      referee: "Test Referee",
      venue_name: "Test Arena",
      referee_stats: [],
    } as MatchDetailResponse["match"],
    data_updated_at: null,
    home_form: [],
    away_form: [],
    reco_published: false,
  };
}

function reportFixture(): MatchReport {
  return {
    match_id: 9002,
    available: true,
    coverage: {
      lineup: false, events: true, shots: true,
      team_stats: true, player_stats: false, momentum: true,
    },
    lineups: [],
    events: [
      {
        event_index: 0, event_type: "Goal", minute: 24, is_added_time: false,
        minutes_added: null, is_home: true, home_score: 1, away_score: 0,
        player_name: "Test Striker", card_type: null, assist_player_name: null,
        sub_in_player_name: null, sub_out_player_name: null, half_kind: null,
        is_own_goal: false,
      },
    ],
    shots: [],
    team_stats: [
      { team_id: 1001, is_home: true, period: "All", possession: 61,
        expected_goals: 2.31, total_shots: 14, shots_on_target: 6,
        touches_opp_box: 28 },
      { team_id: 1002, is_home: false, period: "All", possession: 39,
        expected_goals: 0.87, total_shots: 5, shots_on_target: 1,
        touches_opp_box: 11 },
    ] as MatchReport["team_stats"],
    team_stats_by_half: [],
    player_stats: [],
    momentum: [{ minute: 1, value: 10 }, { minute: 2, value: -5 }],
    top_rated: {
      player_id: "p100", name: "Test Striker", team_id: 1001,
      is_home: true, rating: 7.7, shirt_number: "9", is_official: false,
    },
  } as MatchReport;
}

function renderFinishedDetail() {
  return render(
    <MatchDetailBody
      idNum={9002}
      detail={detailFixture()}
      analysis={null}
      report={reportFixture()}
      preview={null}
      returnTo="/matches"
      returnLabel="返回"
      previousMatch={null}
      nextMatch={null}
    />,
  );
}

describe("已完赛「总览」纵向顺序", () => {
  it("势头图 → 重点数据 → 最高评分 → 关键事件 → 比赛信息 → 裁判 → 数据倾向", () => {
    renderFinishedDetail();
    const panel = document.getElementById("match-panel-overview")!;
    const headings = Array.from(panel.querySelectorAll("h2, h3")).map(
      (el) => el.textContent ?? "",
    );
    const expectOrder = [
      "势头图",
      "重点数据",
      "最高评分",
      "关键事件",
      "比赛信息",
      "裁判",
      "数据倾向",
    ];
    const indices = expectOrder.map((t) =>
      headings.findIndex((h) => h.includes(t)),
    );
    // 每个板块都存在
    expectOrder.forEach((t, i) => {
      expect(indices[i], `板块「${t}」缺失,headings=${JSON.stringify(headings)}`).toBeGreaterThanOrEqual(0);
    });
    // 且严格按序
    for (let i = 1; i < indices.length; i++) {
      expect(indices[i]).toBeGreaterThan(indices[i - 1]);
    }
  });

  it("总览里比赛信息/裁判卡只出现一次(HighlightsGroup 不再重复渲染)", () => {
    renderFinishedDetail();
    const panel = document.getElementById("match-panel-overview")!;
    expect(panel.querySelectorAll('[data-testid="match-info-card"]').length).toBe(1);
    expect(panel.querySelectorAll('[data-testid="referee-card"]').length).toBe(1);
  });

  it("「查看全部数据 →」切到统计 tab;「查看完整时间线 →」切到事件 tab", async () => {
    renderFinishedDetail();
    const statsTab = document.getElementById("match-tab-stats")!;
    expect(statsTab.getAttribute("aria-selected")).toBe("false");
    screen.getByText("查看全部数据 →").click();
    // React 18 自动批处理下点击是同步 setState + re-render
    await vi.waitFor(() =>
      expect(
        document.getElementById("match-tab-stats")!.getAttribute("aria-selected"),
      ).toBe("true"),
    );

    screen.getByText("查看完整时间线 →").click();
    await vi.waitFor(() =>
      expect(
        document.getElementById("match-tab-events")!.getAttribute("aria-selected"),
      ).toBe("true"),
    );
  });
});

describe("「射门」tab 顺序(真实 MatchShotsSection,不用上面的 mock)", () => {
  it("射门落点在最顶部,势头图不再出现在该 tab", async () => {
    // 本用例需要真实组件——按文件级 mock 之外单独动态导入真实实现
    const { MatchShotsSection: RealShots } = await vi.importActual<
      typeof import("@/components/matches/MatchShotsSection")
    >("@/components/matches/MatchShotsSection");

    // ShotMapChart 懒挂载依赖 IntersectionObserver/ResizeObserver,jsdom 没有
    vi.stubGlobal("IntersectionObserver", class {
      observe() {}
      disconnect() {}
      unobserve() {}
    });
    vi.stubGlobal("ResizeObserver", class {
      observe() {}
      disconnect() {}
      unobserve() {}
    });

    const shots = [
      {
        player_id: "p1", player_name: "球员", team_id: 1, is_home: true,
        minute: 10, period: "FirstHalf", x: 90, y: 34, xg: 0.3, xgot: null,
        situation: "RegularPlay", outcome: "Goal", shot_type: "RightFoot",
        is_blocked: null, is_on_target: null,
        is_own_goal: false, is_own_goal_inferred: false,
      },
    ] as MatchReport["shots"];

    const { container } = render(
      <RealShots
        shots={shots}
        lineups={[]}
        homeName="主队"
        awayName="客队"
      />,
    );
    const titles = Array.from(container.querySelectorAll("h2")).map(
      (el) => el.textContent,
    );
    expect(titles[0]).toBe("射门落点");
    expect(titles).toEqual(["射门落点", "射门威胁时间轴", "xG 累积对抗"]);
    expect(titles).not.toContain("势头图");

    vi.unstubAllGlobals();
  });
});

describe("已完赛 tab 拆分(2026-08-25:数据可视化/赔率不再堆在总览底部)", () => {
  it("七个 tab 按序:总览/射门/统计/阵容/事件/分析/赔率", () => {
    renderFinishedDetail();
    const labels = Array.from(
      document.querySelectorAll('[role="tablist"][aria-label="比赛内容切换"] [role="tab"]'),
    ).map((t) => t.textContent);
    expect(labels).toEqual(["总览", "射门", "统计", "阵容", "事件", "分析", "赔率"]);
  });

  it("总览不再包含数据可视化与赔率内容(只留数据倾向)", () => {
    renderFinishedDetail();
    const panel = document.getElementById("match-panel-overview")!;
    expect(panel.textContent).not.toContain("数据可视化");
    expect(panel.querySelector('[data-testid="mock-odds-timeline"]')).toBeNull();
    expect(panel.textContent).toContain("数据倾向");
  });

  it("「分析」tab 承接数据可视化;「赔率」tab 承接赔率快照", () => {
    renderFinishedDetail();
    const analysis = document.getElementById("match-panel-analysis")!;
    expect(analysis.textContent).toContain("数据可视化");
    const odds = document.getElementById("match-panel-odds")!;
    expect(odds.querySelector('[data-testid="mock-odds-timeline"]')).not.toBeNull();
    expect(odds.textContent).toContain("数据来源与说明");
  });

  it("「分析」tab 不渲染预计阵容(概念对已完赛不适用,真实首发在阵容 tab)", () => {
    renderFinishedDetail();
    const analysis = document.getElementById("match-panel-analysis")!;
    expect(analysis.textContent).not.toContain("预计阵容");
    // fixture 的 preview 是 null → DataGroup 渲染诚实空态;这条主要钉的是
    // 标题层面不出现"预计阵容"字样(有 preview 时的 pill 移除由
    // match-data-tabs.test.tsx 的"不传 lineup"用例覆盖)。
  });
});
