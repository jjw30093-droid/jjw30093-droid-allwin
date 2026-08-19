import { describe, expect, it } from "vitest";
import {
  buildMatchesApiQuery,
  buildMatchesHref,
  defaultWindowFor,
  isWindowAutoWidenEligible,
  type MatchFilters,
} from "@/lib/match-filters";

const base: MatchFilters = {
  status: "upcoming",
  window: "7d",
  page: 1,
};

describe("buildMatchesApiQuery", () => {
  it("SSR 与浏览器端会员刷新必须拼出完全相同的 query 串,否则两者看到的比赛集合会不一致", () => {
    const filters: MatchFilters = {
      ...base,
      league: 223,
      season: "2026",
      date: "2026-08-15",
      content: "odds",
      q: "阿森纳",
      page: 2,
    };
    const qs = buildMatchesApiQuery(filters, { limit: 20 });
    const params = new URLSearchParams(qs);
    expect(params.get("league_id")).toBe("223"); // 前端字段名 league,后端接口字段名 league_id
    expect(params.get("season")).toBe("2026");
    expect(params.get("date")).toBe("2026-08-15");
    expect(params.get("content")).toBe("odds");
    expect(params.get("q")).toBe("阿森纳");
    // 本条用例护的是"SSR 与浏览器端拼出同一个串"这个不变量,window 的具体
    // 取值只是顺带断言。2026-08-19 起 date 存在时 window 一律发 all(见下面
    // 「日期筛选选过去的日期不再是白板页」那组:date 已经把范围钉死到一天,
    // 再 AND 一个向未来的窗会让"选过去的日期"恒为空白页),而这个 fixture
    // 恰好带了 date,所以期望值从 "7d" 改成 "all"。这不是放松断言——parity
    // 与其余七个参数的透传断言一条没动,window 仍然被精确断言。
    expect(params.get("window")).toBe("all");
    expect(params.get("limit")).toBe("20");
    expect(params.get("offset")).toBe("20"); // (page-1)*limit
  });

  it("windowOverride 只换 window,不影响其余参数(赛前赛季间歇期自动放宽用)", () => {
    const qs = buildMatchesApiQuery(base, { limit: 20, windowOverride: "all" });
    expect(new URLSearchParams(qs).get("window")).toBe("all");
  });

  it("status='all' 时不下发 status 参数(与后端默认行为对齐)", () => {
    const qs = buildMatchesApiQuery({ ...base, status: "all" }, { limit: 20 });
    expect(new URLSearchParams(qs).has("status")).toBe(false);
  });
});

describe("isWindowAutoWidenEligible", () => {
  it("默认视图(未显式选 window,且无 date/season/q)才允许 0 场时自动放宽", () => {
    expect(isWindowAutoWidenEligible(base, false)).toBe(true);
  });

  it("用户显式选了 window(即使值恰好等于默认的 7d)不允许自动放宽", () => {
    // 这条防的是真实 bug 类别:filters.window 解析后总有值(默认 '7d'),
    // 不能靠它判断"用户是否显式传了 window"——必须传显式标志。
    expect(isWindowAutoWidenEligible(base, true)).toBe(false);
  });

  it("已选日期/赛季/搜索词时不自动放宽 —— 那是用户主动缩小范围,不是无意间选中空窗口", () => {
    expect(isWindowAutoWidenEligible({ ...base, date: "2026-08-15" }, false)).toBe(false);
    expect(isWindowAutoWidenEligible({ ...base, season: "2026" }, false)).toBe(false);
    expect(isWindowAutoWidenEligible({ ...base, q: "阿森纳" }, false)).toBe(false);
  });

  it("非 upcoming 状态不自动放宽(已完赛/全部本来就该按用户选择走)", () => {
    expect(isWindowAutoWidenEligible({ ...base, status: "finished" }, false)).toBe(false);
    expect(isWindowAutoWidenEligible({ ...base, status: "all" }, false)).toBe(false);
  });
});

/**
 * 赛果视图:默认 window 随 status 而变(2026-08-19,「赛果」入口 + 向过去的时间窗)。
 *
 * 真实缺陷:window 的 3d/7d 是严格向未来的 [now, now+N),所以
 * status=finished 配默认的 7d 恒为 0 场,而 isWindowAutoWidenEligible 又
 * 明确排除 finished(见上面那组用例,那条是对的:赛果为空就该如实说)——
 * 结果是"已完赛"这个筛选除非同时手动带上 window=all,否则永远是白板。
 *
 * 修法是让默认 window 随 status 走。这里最容易埋的雷是:URL 省略规则
 * (buildMatchesHref)与解析补默认值(app/matches/page.tsx)是两处独立代码,
 * 只要它们不共用同一个 defaultWindowFor,就会出现"URL 看起来完全正常、
 * 渲染出来却是 0 场"的静默空白页。所以本组的核心是往返恒等。
 */
describe("defaultWindowFor:默认时间窗随状态而变", () => {
  it("赛程默认未来七天;赛果默认全部(最新在前,第一页天然就是最近的赛果)", () => {
    expect(defaultWindowFor("upcoming")).toBe("7d");
    expect(defaultWindowFor("finished")).toBe("all");
    expect(defaultWindowFor("all")).toBe("all");
  });

  it("赛果默认刻意不用 past7d —— 赛季间歇期真的会是 0 场,而 all 永不空页", () => {
    // 这条不是重复上一条:它钉的是"为什么不是 past7d"这个决定本身。
    // past7d 仍然作为时间 chip 提供,只是不当默认值。
    expect(defaultWindowFor("finished")).not.toBe("past7d");
  });
});

describe("buildMatchesHref × defaultWindowFor 往返恒等(静默空白页的唯一防线)", () => {
  /** 复刻 app/matches/page.tsx 的解析规则:URL 缺省时按 status 补默认窗口。 */
  function parseHref(href: string): { status: MatchFilters["status"]; window: MatchFilters["window"] } {
    const qs = new URLSearchParams(href.split("?")[1] ?? "");
    const raw = qs.get("status");
    const status: MatchFilters["status"] =
      raw === "finished" || raw === "all" ? raw : "upcoming";
    return {
      status,
      window: (qs.get("window") as MatchFilters["window"]) ?? defaultWindowFor(status),
    };
  }

  it.each(["upcoming", "finished", "all"] as const)(
    "status=%s:build 出来的 URL 再解析回去,status 与 window 都必须原样还原",
    (status) => {
      const filters: MatchFilters = { status, window: defaultWindowFor(status), page: 1 };
      expect(parseHref(buildMatchesHref(filters, {}))).toEqual({
        status,
        window: defaultWindowFor(status),
      });
    },
  );

  it("从赛程切到赛果时必须同时改写 window,不能留下 7d(否则恒为 0 场)", () => {
    const upcoming: MatchFilters = { status: "upcoming", window: "7d", page: 1 };
    const href = buildMatchesHref(upcoming, {
      status: "finished",
      window: defaultWindowFor("finished"),
      page: 1,
    });
    expect(parseHref(href)).toEqual({ status: "finished", window: "all" });
    expect(href).not.toContain("window=7d");
  });

  it("非默认窗口必须写进 URL(赛果选了近七天,刷新后不能被打回全部)", () => {
    const href = buildMatchesHref(
      { status: "finished", window: "past7d", page: 1 },
      {},
    );
    expect(new URLSearchParams(href.split("?")[1]).get("window")).toBe("past7d");
    expect(parseHref(href)).toEqual({ status: "finished", window: "past7d" });
  });

  it("旧链接 /matches?status=finished&window=all 的行为逐字不变(可能已被收藏/被抓取)", () => {
    expect(parseHref("/matches?status=finished&window=all")).toEqual({
      status: "finished",
      window: "all",
    });
  });
});

describe("日期筛选选过去的日期不再是白板页", () => {
  it("date 存在时 window 一律发 all —— date 已经把范围钉死到一天,再 AND 一个未来窗恒为空", () => {
    // 真实缺陷(生产实测):/matches?date=2026-08-16 渲染出空页,而那天真实
    // 有 31 场已完赛比赛。日期表单的 hidden 字段会把 status=upcoming 与
    // window=7d 一起回传,三者在 SQL 里是 AND。
    const qs = buildMatchesApiQuery(
      { status: "upcoming", window: "7d", date: "2026-08-16", page: 1 },
      { limit: 20 },
    );
    expect(new URLSearchParams(qs).get("window")).toBe("all");
    expect(new URLSearchParams(qs).get("date")).toBe("2026-08-16");
  });

  it("没有 date 时 window 照常下发,不受影响", () => {
    const qs = buildMatchesApiQuery({ status: "upcoming", window: "7d", page: 1 }, { limit: 20 });
    expect(new URLSearchParams(qs).get("window")).toBe("7d");
  });

  it("windowOverride 优先级仍高于 date 规则(自动放宽路径不被打断)", () => {
    const qs = buildMatchesApiQuery(
      { status: "upcoming", window: "7d", date: "2026-08-16", page: 1 },
      { limit: 20, windowOverride: "all" },
    );
    expect(new URLSearchParams(qs).get("window")).toBe("all");
  });
});
