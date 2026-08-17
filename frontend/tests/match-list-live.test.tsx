/**
 * /matches 列表页移动端信息架构重排(P3.B,2026-08-16)。
 *
 * 真实回归:390px 视口下所有筛选控件(状态/时间/内容/联赛/赛季/日期/球队搜索)
 * 默认全部展开摊平渲染,第一场比赛要滑到 y≈495px 才出现。
 *
 * 关键断言:
 * - 低频筛选(状态、赛季、日期、球队搜索)收进默认折叠的 <details> "更多筛选",
 *   不占首屏空间;高频筛选(时间、内容、联赛)留在主筛选行始终可见;
 * - 折叠区展开后提交筛选仍是纯 GET 表单 + hidden 字段回传其它已选筛选值
 *   (无 JS 也可用这个既有特性不能丢);
 * - 比赛行按开球日期(北京时间,缺失 kickoff 时退回 date_utc)分组,组间插入
 *   日期小标题,且分组只是纯前端视觉分组——不改变数组顺序、不合并非相邻的
 *   同一天比赛(证明没有偷偷做全局排序/去重)。
 */

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MatchListLive } from "@/components/matches/MatchListLive";
import type { LeagueInfo, MatchSummary } from "@/lib/api-v1";
import type { MatchFilters } from "@/lib/match-filters";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function stubFetchRejecting() {
  // 挂载后的浏览器端刷新(clientFetch)在测试里应该静默失败并保留 SSR 传入的
  // initial* props——这是组件既有的降级设计,不是这次要验证的行为,只是不能
  // 让它在测试里发起真实网络请求。
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.reject(new Error("no network in test"))),
  );
}

function baseFilters(overrides: Partial<MatchFilters> = {}): MatchFilters {
  return {
    status: "upcoming",
    window: "7d",
    page: 1,
    ...overrides,
  };
}

function baseMatch(overrides: Partial<MatchSummary> = {}): MatchSummary {
  return {
    match_id: 1,
    league_id: 47,
    season: "2025-2026",
    date_utc: "2026-08-20",
    kickoff_at_utc: "2026-08-20T19:00:00Z",
    round: null,
    status: "NotStarted",
    home: { team_id: 10, name: "主队FC", name_en: null, crest_url: null },
    away: { team_id: 20, name: "客队FC", name_en: null, crest_url: null },
    home_score: null,
    away_score: null,
    sync_state: null,
    data_updated_at: null,
    last_success_sync_at: null,
    next_planned_sync_at: null,
    probability_source: null,
    odds_observation_count: null,
    odds_coverage_tier: null,
    odds_last_observed_at: null,
    odds_freshness_state: null,
    win_probability: null,
    ...overrides,
  } as MatchSummary;
}

const leagues: LeagueInfo[] = [
  {
    league_id: 47,
    code: "epl",
    name_zh: "英超",
    name_en: "Premier League",
    current_season: "2025/2026",
    available_seasons: ["2025/2026", "2024/2025"],
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

function renderList(props: {
  filters?: Partial<MatchFilters>;
  matches?: MatchSummary[];
  seasonOptions?: string[];
}) {
  stubFetchRejecting();
  return render(
    <MatchListLive
      filters={baseFilters(props.filters)}
      pageSize={20}
      autoWidenEligible={false}
      seasonOptions={props.seasonOptions ?? ["2025/2026", "2024/2025"]}
      initialLeagues={leagues}
      initialMatches={props.matches ?? [baseMatch()]}
      initialTotal={props.matches?.length ?? 1}
      initialWindowWidened={false}
    />,
  );
}

describe("更多筛选折叠(低频筛选默认不占首屏空间)", () => {
  it("默认(无 season/date/q,status=upcoming)时,更多筛选 <details> 是关闭的", () => {
    const { container } = renderList({});
    const details = container.querySelector("details");
    expect(details).not.toBeNull();
    expect(details!.open).toBe(false);
  });

  it("状态/赛季/日期/球队搜索四个控件都在更多筛选区块内", () => {
    const { container } = renderList({});
    const details = container.querySelector("details")!;
    const scoped = within(details);
    expect(scoped.getByText("状态")).not.toBeNull();
    expect(scoped.getByText("赛季")).not.toBeNull();
    expect(scoped.getByLabelText("日期")).not.toBeNull();
    expect(scoped.getByLabelText("球队")).not.toBeNull();
  });

  it("时间/内容/联赛三组主筛选留在 details 之外,始终可见", () => {
    const { container } = renderList({});
    const details = container.querySelector("details")!;
    expect(within(container).getByText("时间")).not.toBeNull();
    expect(within(container).getByText("联赛")).not.toBeNull();
    expect(within(container).getByText("内容")).not.toBeNull();
    // 三组主筛选标签本身不应该出现在 details 内部
    expect(within(details).queryByText("时间")).toBeNull();
    expect(within(details).queryByText("联赛")).toBeNull();
    expect(within(details).queryByText("内容")).toBeNull();
  });

  it("已经显式选择了更多筛选里的条件(如 status=finished)时,details 默认展开,不把用户已选筛选藏起来", () => {
    const { container } = renderList({ filters: { status: "finished" } });
    const details = container.querySelector("details")!;
    expect(details.open).toBe(true);
  });

  it("折叠区展开后的日期/球队搜索表单仍是纯 GET 表单,且带上其它已选筛选的 hidden 字段(无 JS 也可用)", () => {
    const { container } = renderList({
      filters: { league: 47, season: "2025/2026", status: "finished", q: "阿森纳" },
    });
    const forms = container.querySelectorAll("form");
    expect(forms.length).toBeGreaterThanOrEqual(2);
    forms.forEach((form) => {
      expect(form.getAttribute("method")).toBe("get");
      expect(form.getAttribute("action")).toBe("/matches");
    });
    // 日期表单要把 league/season/status 都用 hidden 字段回传,否则提交后筛选会丢失/漂移
    const dateForm = screen.getByLabelText("日期").closest("form")!;
    expect(dateForm.querySelector('input[name="league"]')).toHaveProperty("value", "47");
    expect(dateForm.querySelector('input[name="season"]')).toHaveProperty(
      "value",
      "2025/2026",
    );
    expect(dateForm.querySelector('input[name="status"]')).toHaveProperty(
      "value",
      "finished",
    );
  });
});

describe("比赛行按开球日期(北京时间)分组", () => {
  it("组间插入日期小标题,组内顺序与传入的 matches 数组顺序完全一致(纯前端视觉分组,不重新排序)", () => {
    // 刻意让同一天的比赛不相邻(day2 出现在两个 day1 之间),用来证明分组
    // 不是"整体按日期归并"——如果被归并,day1 的两场会被拼到一起,标题只会
    // 出现 2 次而不是 3 次。
    const matches = [
      baseMatch({ match_id: 1, kickoff_at_utc: "2026-08-15T19:00:00Z" }), // 北京 8/16 周日 03:00
      baseMatch({ match_id: 2, kickoff_at_utc: "2026-08-16T11:00:00Z" }), // 北京 8/16 周日 19:00 (同一天)
      baseMatch({ match_id: 3, kickoff_at_utc: "2026-08-16T19:00:00Z" }), // 北京 8/17 周一 03:00
      baseMatch({ match_id: 4, kickoff_at_utc: "2026-08-15T11:00:00Z" }), // 北京 8/15 周六 19:00
    ];
    const { container } = renderList({ matches });

    const headings = Array.from(container.querySelectorAll('[data-testid="date-heading"]')).map(
      (el) => el.textContent,
    );
    expect(headings).toEqual(["8月16日 周日", "8月17日 周一", "8月15日 周六"]);

    // 比赛行本身的相对顺序不变(match_id 1,2,3,4 依次出现)
    const rows = Array.from(container.querySelectorAll('a[href*="/matches/"]'));
    const ids = rows.map((r) => r.getAttribute("href"));
    expect(ids[0]).toContain("/matches/1");
    expect(ids[1]).toContain("/matches/2");
    expect(ids[2]).toContain("/matches/3");
    expect(ids[3]).toContain("/matches/4");
  });

  it("缺失精确 kickoff 时退回 date_utc 分组,不编造精确时刻", () => {
    const matches = [
      baseMatch({ match_id: 5, kickoff_at_utc: null, date_utc: "2026-08-21" }),
    ];
    const { container } = renderList({ matches });
    const headings = Array.from(
      container.querySelectorAll('[data-testid="date-heading"]'),
    ).map((el) => el.textContent);
    expect(headings).toEqual(["8月21日 周五"]);
  });
});

describe("权限口径修正(2026-08-16):LeagueInfo 不再有 entitlement/accessible/requires_login", () => {
  it("联赛筛选 chip 不出现'登录'徽标(所有联赛对匿名同等可访问)", () => {
    const { container } = renderList({});
    expect(within(container).queryByText("登录")).toBeNull();
  });

  it("筛选结果为空时不出现'登录后查看…完整赔率与概率'引导链接", () => {
    const { container } = renderList({
      matches: [],
      filters: { league: 223 },
    });
    expect(container.textContent).not.toMatch(/登录后查看/);
    expect(container.textContent).not.toMatch(/完整赔率与概率/);
  });
});
