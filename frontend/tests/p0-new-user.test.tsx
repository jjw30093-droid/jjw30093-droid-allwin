/**
 * 2026-10-01 站长 P0(新用户视角整改)的纯函数与组件单测:
 * - 数据倾向卡只展示"有方向且历史命中率 ≥ 50%"的卡;
 * - 推荐单列表用标准联赛名,不直接显示人工标题;
 * - 赛程卡显示联赛名;赛程 API 请求按时间排(sort=time)。
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { isShowableMarketCard, type MarketCardData } from "@/lib/market-cards";
import { slipLeagueLabel } from "@/lib/reco-labels";
import { MatchRow } from "@/components/matches/MatchRow";
import { buildMatchesApiQuery } from "@/lib/match-filters";
import type { MatchSummary } from "@/lib/api-v1";

afterEach(() => cleanup());

function card(over: Partial<MarketCardData>): MarketCardData {
  return {
    market: "cards", label: "罚牌", line: 3.5, line_source: "statistical",
    data_quality: "ok", signal_grade: "★★", lean: "under", hit_rate: 0.56, sample_size: 300,
    driver_factors: [], driver_factors_away: [], lines: [],
    ...over,
  } as MarketCardData;
}

describe("数据倾向卡展示门槛", () => {
  it("有方向、有星级、命中率 ≥ 50% 才展示", () => {
    expect(isShowableMarketCard(card({}))).toBe(true);
    expect(isShowableMarketCard(card({ hit_rate: 0.5 }))).toBe(true);
  });
  it("命中率不到一半的不展示(线上出现过'偏小 · 48%')", () => {
    expect(isShowableMarketCard(card({ hit_rate: 0.48 }))).toBe(false);
  });
  it("未标定 / 样本不足 / 没方向 / 没命中率的不展示", () => {
    expect(isShowableMarketCard(card({ data_quality: "no_calibration", signal_grade: null }))).toBe(false);
    expect(isShowableMarketCard(card({ data_quality: "insufficient_sample" }))).toBe(false);
    expect(isShowableMarketCard(card({ data_quality: "no_history" }))).toBe(false);
    expect(isShowableMarketCard(card({ signal_grade: null }))).toBe(false);
    expect(isShowableMarketCard(card({ lean: null }))).toBe(false);
    expect(isShowableMarketCard(card({ hit_rate: null }))).toBe(false);
  });
});

describe("推荐单联赛标签", () => {
  it("用腿上的标准联赛名,不用人工标题(荷兰甲 → 荷甲)", () => {
    expect(slipLeagueLabel({ title: "荷兰甲", legs: [{ league_name_zh: "荷甲" }] })).toBe("荷甲");
    expect(slipLeagueLabel({ title: "周五早场日职联", legs: [{ league_name_zh: "日职联" }] })).toBe("日职联");
  });
  it("多个联赛去重后用「·」连接", () => {
    expect(
      slipLeagueLabel({ title: "串", legs: [{ league_name_zh: "英超" }, { league_name_zh: "西甲" }, { league_name_zh: "英超" }] }),
    ).toBe("英超 · 西甲");
  });
  it("一条都取不到(站外赛事)才退回原标题", () => {
    expect(slipLeagueLabel({ title: "站外赛事", legs: [{ league_name_zh: null }, {}] })).toBe("站外赛事");
  });
});

describe("赛程卡联赛名", () => {
  const m = {
    match_id: 1, league_id: 268, round: "21", status: "NotStarted",
    kickoff_at_utc: "2026-10-02T23:00:00Z", date_utc: "2026-10-02",
    home: { team_id: 1, name: "圣保罗", crest_url: null }, away: { team_id: 2, name: "桑托斯", crest_url: null },
    home_score: null, away_score: null, win_probability: null,
  } as unknown as MatchSummary;

  it("showLeague 时显示联赛名 + 轮次", () => {
    render(<MatchRow match={m} showLeague />);
    expect(screen.getByTestId("row-league").textContent).toContain("巴甲 · 第21轮");
  });
  it("默认(联赛自己的页面)不显示联赛名", () => {
    render(<MatchRow match={m} />);
    expect(screen.queryByTestId("row-league")).toBeNull();
  });
  it("进行中等非常规状态仍写出来", () => {
    render(<MatchRow match={{ ...m, status: "InPlay" } as MatchSummary} showLeague />);
    expect(screen.getByTestId("row-league").textContent).toMatch(/巴甲 · 第21轮 · .+/);
  });
});

describe("赛程 API 请求", () => {
  it("按开球时间排(sort=time),不用'有赔率优先'", () => {
    const qs = buildMatchesApiQuery(
      { date: undefined, league: undefined, season: undefined, status: "upcoming", window: "7d", content: undefined, q: undefined, page: 1 },
      { limit: 20 },
    );
    expect(new URLSearchParams(qs).get("sort")).toBe("time");
  });
});
