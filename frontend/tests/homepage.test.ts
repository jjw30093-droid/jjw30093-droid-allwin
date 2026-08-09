import { describe, expect, it } from "vitest";
import type { MatchSummary, TrackRecordResponse } from "@/lib/api-v1";
import type { HomeMatchCard } from "@/lib/homepage";
import {
  HOMEPAGE_FEATURE_EVENT,
  publicRecordView,
  selectFeaturedOverride,
  selectHomepageEvidence,
  selectHomepageMatches,
} from "@/lib/homepage";

function match(
  matchId: number,
  kickoff: string,
  probability: number | null = null,
  leagueId = 59,
): HomeMatchCard {
  return {
    match: {
      match_id: matchId,
      league_id: leagueId,
      season: "2026",
      date_utc: kickoff.slice(0, 10),
      kickoff_at_utc: kickoff,
      status: "NotStarted",
      home: { id: matchId * 10, name: `主队${matchId}` },
      away: { id: matchId * 10 + 1, name: `客队${matchId}` },
    } as MatchSummary,
    tip:
      probability == null
        ? null
        : {
            top_outcome: "home",
            top_probability: probability,
            probability_source: "MARKET_BASELINE",
          },
  };
}

describe("selectHomepageMatches", () => {
  it("优先选择有合法公开概率的最近比赛,并从其他比赛中排除", () => {
    const noTipSooner = match(1, "2026-07-30T12:00:00Z");
    const featured = match(2, "2026-07-31T12:00:00Z", 0.58);
    const laterTip = match(3, "2026-08-01T12:00:00Z", 0.52);

    const result = selectHomepageMatches([laterTip, noTipSooner, featured]);

    expect(result.featured?.match.match_id).toBe(2);
    expect(result.secondary.map((card) => card.match.match_id)).not.toContain(2);
    expect(result.secondary.map((card) => card.match.match_id)).toEqual([3, 1]);
  });

  it("没有预测时退化到最近开球比赛并保持分析准备中所需的 null tip", () => {
    const result = selectHomepageMatches([
      match(9, "2026-08-03T12:00:00Z"),
      match(8, "2026-08-02T12:00:00Z"),
    ]);

    expect(result.featured?.match.match_id).toBe(8);
    expect(result.featured?.tip).toBeNull();
  });
});

describe("selectFeaturedOverride(2026-08-07 J 联赛开幕一次性置顶)", () => {
  const pinned = match(
    HOMEPAGE_FEATURE_EVENT.pinnedMatchId,
    HOMEPAGE_FEATURE_EVENT.pinnedKickoffUtc,
    null,
    223,
  );

  it("置顶窗口内(开球前)有该场次则选中,不看有没有公开概率", () => {
    const now = new Date("2026-08-06T12:00:00Z"); // 开球前一天
    const other = match(1, "2026-08-07T09:00:00Z", 0.9, 47);
    const result = selectFeaturedOverride([other, pinned], [], now);
    expect(result?.match.match_id).toBe(HOMEPAGE_FEATURE_EVENT.pinnedMatchId);
  });

  it("置顶窗口内(开球后 2 小时内)仍然选中", () => {
    const now = new Date("2026-08-07T12:00:00Z"); // 开球后 1h35m
    const result = selectFeaturedOverride([pinned], [], now);
    expect(result?.match.match_id).toBe(HOMEPAGE_FEATURE_EVENT.pinnedMatchId);
  });

  it("过了开球 2 小时后,从北欧候选里选离当前时间最近的一场", () => {
    const now = new Date("2026-08-07T13:00:00Z"); // 开球后 2h35m,已过窗口
    const allsvenskan = match(201, "2026-08-07T14:00:00Z", null, 67); // 1h 后
    const eliteserien = match(202, "2026-08-07T16:00:00Z", null, 59); // 3h 后
    const result = selectFeaturedOverride(
      [pinned],
      [eliteserien, allsvenskan],
      now,
    );
    expect(result?.match.match_id).toBe(201);
  });

  it("过了窗口且没有北欧候选时返回 null,交给默认算法兜底", () => {
    const now = new Date("2026-08-07T13:00:00Z");
    expect(selectFeaturedOverride([pinned], [], now)).toBeNull();
  });

  it("置顶窗口内但置顶场次不在候选池里时不伪造,落到北欧回退", () => {
    const now = new Date("2026-08-07T12:00:00Z");
    const allsvenskan = match(201, "2026-08-07T12:30:00Z", null, 67);
    const result = selectFeaturedOverride([], [allsvenskan], now);
    expect(result?.match.match_id).toBe(201);
  });
});

describe("selectHomepageEvidence", () => {
  it("按 form → season_xg → rest 取每类第一条,缺少时不补模拟内容", () => {
    const selected = selectHomepageEvidence([
      { side: "away", kind: "form", text: "客队近期状态" },
      { side: "home", kind: "form", text: "主队近期状态" },
      { side: "both", kind: "rest", text: "两队休息时间" },
    ]);

    expect(selected.map((item) => item.kind)).toEqual(["form", "rest"]);
    expect(selected.map((item) => item.text)).toEqual([
      "客队近期状态",
      "两队休息时间",
    ]);
  });
});

describe("publicRecordView", () => {
  it("零样本与接口失败是两个不同状态", () => {
    const empty = {
      total: 0,
      retracted_count: 0,
      superseded_count: 0,
      limit: 40,
      offset: 0,
      metrics: null,
      samples: [],
    } as TrackRecordResponse;

    expect(publicRecordView(empty)).toEqual({ status: "empty" });
    expect(publicRecordView(null, true)).toEqual({ status: "error" });
  });
});
