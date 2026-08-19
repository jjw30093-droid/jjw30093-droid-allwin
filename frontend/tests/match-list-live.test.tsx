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

/**
 * 赛果视图(2026-08-19,「赛果」入口 + 向过去的时间窗)。
 *
 * 真实缺陷两条:
 * 1. 时间 chip 每个都硬编码 `status:"upcoming"`——用户好不容易切到「已完赛」,
 *    点一下任意时间 chip 就被弹回赛程,等于赛果里根本没有时间筛选;
 * 2. 时间 chip 全是向未来的(今天/明天/未来三天/未来七天/全部未来),对赛果
 *    毫无意义:除「今天」外每一个配 status=finished 都恒为 0 场。
 *
 * 修法是让时间 chip 按 status 分叉,且不改变 chip 行数与行高——移动端首屏
 * 第一场比赛的 y 坐标已经只剩 25px 余量(见 e2e/matches-mobile-first-screen),
 * 多加任何一行都会顶穿那条验收。
 */
describe("赛果视图:时间 chip 按状态分叉,且不再把用户打回赛程", () => {
  function timeChipRow(container: HTMLElement) {
    const label = Array.from(container.querySelectorAll("span")).find(
      (s) => s.textContent === "时间",
    );
    return label!.parentElement!;
  }

  it("status=upcoming 时是原来那五个向未来的 chip(既有行为不许动)", () => {
    const { container } = renderList({});
    const labels = Array.from(timeChipRow(container).querySelectorAll("a")).map(
      (a) => a.textContent,
    );
    expect(labels).toEqual(["今天", "明天", "未来三天", "未来七天", "全部未来"]);
  });

  it("status=finished 时换成向过去的 chip,且数量不变(首屏预算不许被顶穿)", () => {
    const { container } = renderList({ filters: { status: "finished", window: "all" } });
    const labels = Array.from(timeChipRow(container).querySelectorAll("a")).map(
      (a) => a.textContent,
    );
    expect(labels).toEqual(["今天", "昨天", "近三天", "近七天", "全部赛果"]);
    expect(labels).toHaveLength(5);
  });

  it("赛果的「今天」复用既有的 today —— 它本来就是北京自然日、天然双向,不另造 token", () => {
    const { container } = renderList({ filters: { status: "finished", window: "all" } });
    const today = Array.from(timeChipRow(container).querySelectorAll("a")).find(
      (a) => a.textContent === "今天",
    )!;
    expect(today.getAttribute("href")).toContain("window=today");
  });

  it("时间 chip 保留当前状态,不再硬编码 status=upcoming(这是本次修的 bug)", () => {
    const { container } = renderList({ filters: { status: "finished", window: "all" } });
    const hrefs = Array.from(timeChipRow(container).querySelectorAll("a")).map((a) =>
      a.getAttribute("href"),
    );
    hrefs.forEach((href) => {
      expect(href).toContain("status=finished");
      expect(href).not.toContain("status=upcoming");
    });
  });

  it("赛程侧的时间 chip 仍然不带 status 参数(upcoming 是默认值,省略即可)", () => {
    const { container } = renderList({});
    Array.from(timeChipRow(container).querySelectorAll("a")).forEach((a) => {
      expect(a.getAttribute("href")).not.toContain("status=");
    });
  });
});

/**
 * 空态出口:选了一个"已经过去的日期"却停在赛程视图(2026-08-19)。
 *
 * 真实缺陷(生产实测):/matches?date=2026-08-16 渲染出一块没有任何解释的
 * 白板,而那天真实有 31 场已完赛比赛。日期表单的 hidden 字段把
 * status=upcoming 一起回传,date ∧ 未开赛 对一个过去的日期恒为空。
 *
 * 光把 window 放宽还不够(那只解决了三个 AND 里的一个),status 这一半必须
 * 由空态给出出口:如实说明"这天的比赛已经结束了",并给一个到同日赛果的链接。
 * 不是自动改写用户的筛选——那会让 URL 与界面显示的筛选状态不一致。
 */
describe("空态:日期已过去但停在赛程视图时,给出到当天赛果的出口", () => {
  it("date + status=upcoming + 0 场 → 出现指向同一天赛果的链接", () => {
    const { container } = renderList({
      matches: [],
      filters: { date: "2026-08-16", status: "upcoming" },
    });
    const link = Array.from(container.querySelectorAll("a")).find((a) =>
      a.getAttribute("href")?.includes("status=finished"),
    );
    expect(link).toBeTruthy();
    expect(link!.getAttribute("href")).toContain("date=2026-08-16");
    expect(container.textContent).toMatch(/已经结束|赛果/);
  });

  it("已经在赛果视图下的 0 场不再重复给这个出口(那是真的没有比赛)", () => {
    const { container } = renderList({
      matches: [],
      filters: { date: "2026-08-16", status: "finished", window: "all" },
    });
    expect(container.textContent).not.toMatch(/已经结束了/);
  });

  it("没有选日期的 0 场保持原样(不凭空推销赛果)", () => {
    const { container } = renderList({ matches: [], filters: { league: 223 } });
    // 只看空态框内部:更多筛选里的「已完赛」状态 chip 本来就带 status=finished,
    // 它是常设筛选控件,不是这条空态出口。
    const emptyBox = Array.from(container.querySelectorAll("div")).find((d) =>
      d.textContent?.startsWith("没有符合当前筛选条件的比赛"),
    )!;
    expect(emptyBox).toBeTruthy();
    expect(emptyBox.querySelectorAll("a")).toHaveLength(0);
  });
});
