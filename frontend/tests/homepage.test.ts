import { describe, expect, it } from "vitest";
import type { MatchSummary, TrackRecordResponse } from "@/lib/api-v1";
import type { HomeMatchCard } from "@/lib/homepage";
import {
  publicRecordView,
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
      requires_login: false,
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

describe("selectHomepageMatches:data-aware(空页面永远不做重点)", () => {
  const now = new Date("2026-08-12T12:00:00Z");

  it("有射门史的比赛顶掉时间更近但什么数据都没有的比赛", () => {
    // 实测背景:未来 7 天 78 场里 24 场(31%)射门与赔率都没有,而本周赛程最多的
    // 四个联赛(英冠/巴甲/葡超/荷甲)在 dim_match 里 0 场完赛、0 行射门 ——
    // 旧的纯"时间就近"规则会让约 1/3 的首页重点卡指向一场空白比赛。
    const emptySooner = match(1, "2026-08-12T12:10:00Z");
    const richLater = match(2, "2026-08-14T12:00:00Z");

    const result = selectHomepageMatches([emptySooner, richLater], now, {
      withShots: new Set([2]),
    });

    expect(result.featured?.match.match_id).toBe(2);
  });

  it("同为有数据时仍按开球时间就近(富集度只做粗分档,不做连续排序)", () => {
    const soon = match(3, "2026-08-12T12:30:00Z");
    const later = match(4, "2026-08-15T12:00:00Z");

    const result = selectHomepageMatches([later, soon], now, {
      withShots: new Set([3, 4]),
    });

    expect(result.featured?.match.match_id).toBe(3);
  });

  it("赔率也算数据:有赔率的比赛顶掉两样都没有的", () => {
    const nothing = match(5, "2026-08-12T12:05:00Z");
    const withOdds = match(6, "2026-08-13T12:00:00Z");
    withOdds.match.odds_coverage_tier = "open_close_only";

    const result = selectHomepageMatches([nothing, withOdds], now, {
      withShots: new Set(),
    });

    expect(result.featured?.match.match_id).toBe(6);
  });

  it("全都没有数据时退化为纯时间就近(不因此崩溃或空选)", () => {
    const result = selectHomepageMatches(
      [match(7, "2026-08-14T12:00:00Z"), match(8, "2026-08-12T13:00:00Z")],
      now,
      { withShots: new Set() },
    );

    expect(result.featured?.match.match_id).toBe(8);
  });
});

describe("selectHomepageMatches(2026-08-12 改版:按开球时间就近排序)", () => {
  it("选中开球时间离当前最近的一场,不论是刚开球还是即将开球", () => {
    const now = new Date("2026-08-12T12:00:00Z");
    const justKickedOff = match(1, "2026-08-12T11:30:00Z"); // 30min 前
    const soon = match(2, "2026-08-12T12:20:00Z"); // 20min 后(离 now 更近)
    const farAway = match(3, "2026-08-15T12:00:00Z");

    const result = selectHomepageMatches([justKickedOff, soon, farAway], now);

    expect(result.featured?.match.match_id).toBe(2);
    expect(result.secondary.map((c) => c.match.match_id)).toEqual([1, 3]);
  });

  it("同一时刻撞车时按联赛档位打平(英超 > 西甲/意甲/德甲/法甲 > 巴甲 > 其它)", () => {
    const now = new Date("2026-08-12T12:00:00Z");
    const eliteserien = match(1, "2026-08-12T12:05:00Z", null, 59); // 挪超
    const laliga = match(2, "2026-08-12T12:05:00Z", null, 87); // 西甲
    const epl = match(3, "2026-08-12T12:05:00Z", null, 47); // 英超

    const result = selectHomepageMatches([eliteserien, laliga, epl], now);

    expect(result.featured?.match.match_id).toBe(3); // 英超优先
    expect(result.secondary.map((c) => c.match.match_id)).toEqual([2, 1]);
  });

  it("五大联赛休赛期(候选池只有小联赛)自动退化为纯粹的时间就近", () => {
    const now = new Date("2026-08-12T00:00:00Z");
    const result = selectHomepageMatches(
      [match(9, "2026-08-13T12:00:00Z"), match(8, "2026-08-12T06:00:00Z")],
      now,
    );

    expect(result.featured?.match.match_id).toBe(8); // 更接近 now
    expect(result.featured?.tip).toBeNull(); // tip 字段照常透传,不参与排序
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
