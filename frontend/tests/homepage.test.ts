import { describe, expect, it } from "vitest";
import type { MatchSummary, TrackRecordResponse, WinProbability } from "@/lib/api-v1";
import type { HomeMatchCard } from "@/lib/homepage";
import {
  publicRecordView,
  selectFeaturedMatch,
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

/** selectFeaturedMatch 专用 fixture:MatchSummary 不再有 requires_login 字段
 * (2026-08-16 权限口径修正,后端对任何人恒返回完整内容),这里只保留
 * win_probability 这一个真正影响选场的信号。 */
function heroCard(
  matchId: number,
  kickoff: string,
  opts: { hasProbability: boolean; leagueId?: number },
): HomeMatchCard {
  const winProbability: WinProbability | undefined = opts.hasProbability
    ? { p_home: 0.4, p_draw: 0.3, p_away: 0.3, observed_at: kickoff }
    : undefined;
  return {
    match: {
      match_id: matchId,
      league_id: opts.leagueId ?? 47,
      season: "2026",
      date_utc: kickoff.slice(0, 10),
      kickoff_at_utc: kickoff,
      status: "NotStarted",
      home: { id: matchId * 10, name: `主队${matchId}` },
      away: { id: matchId * 10 + 1, name: `客队${matchId}` },
      win_probability: winProbability,
    } as MatchSummary,
    tip: null,
  };
}

describe("selectFeaturedMatch:候选池不再有任何比赛处于锁定状态时仍能确定性选场", () => {
  /**
   * 取代旧的 selectHeroPair(freeCard/lockedCard 双卡对照)测试。2026-08-16
   * 权限口径修正后,MatchSummary 不再有 requires_login 字段——不存在"这场
   * 比赛被锁定,看不到概率"这个产品状态了,选场逻辑必须在没有任何比赛处于
   * 锁定状态的输入下,仍然确定性地选出一张真正有数据的重点比赛。
   */
  it("12 场候选里只有 1 场已发布概率(排在第 11 位)时,仍能定位到这一场", () => {
    const now = new Date("2026-08-16T00:00:00Z");
    const cards: HomeMatchCard[] = [
      ...Array.from({ length: 8 }, (_, i) =>
        heroCard(9100 + i + 1, `2026-08-16T0${i + 1}:00:00Z`, { hasProbability: false }),
      ),
      heroCard(9109, "2026-08-16T09:00:00Z", { hasProbability: false }),
      heroCard(9110, "2026-08-16T10:00:00Z", { hasProbability: false }),
      heroCard(9111, "2026-08-16T11:00:00Z", { hasProbability: true }), // 目标
      heroCard(9112, "2026-08-16T12:00:00Z", { hasProbability: false }),
    ];

    const { featured, secondary } = selectFeaturedMatch(cards, now);

    expect(featured?.match.match_id).toBe(9111);
    expect(featured?.match.win_probability).toBeTruthy();
    // secondary 已排除 featured,按开球时间排列,且不重复出现。
    const secondaryIds = secondary.map((c) => c.match.match_id);
    expect(secondaryIds).not.toContain(9111);
    expect(secondaryIds.length).toBe(cards.length - 1);
    expect(new Set(secondaryIds).size).toBe(secondaryIds.length);
  });

  it("没有任何比赛已发布概率时,仍确定性退化为开球时间最近的一场(不返回 null、不报错)", () => {
    const now = new Date("2026-08-16T00:00:00Z");
    const cards: HomeMatchCard[] = Array.from({ length: 3 }, (_, i) =>
      heroCard(9200 + i, `2026-08-16T0${i + 1}:00:00Z`, { hasProbability: false }),
    );

    const { featured, secondary } = selectFeaturedMatch(cards, now);

    expect(featured?.match.match_id).toBe(9200);
    expect(secondary.map((c) => c.match.match_id)).toEqual([9201, 9202]);
  });

  it("候选池为空时诚实返回 null,不臆造数据", () => {
    const { featured, secondary } = selectFeaturedMatch([], new Date("2026-08-16T00:00:00Z"));

    expect(featured).toBeNull();
    expect(secondary).toEqual([]);
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
