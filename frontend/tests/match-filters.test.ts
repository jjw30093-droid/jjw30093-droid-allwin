import { describe, expect, it } from "vitest";
import {
  buildMatchesApiQuery,
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
    expect(params.get("window")).toBe("7d");
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
