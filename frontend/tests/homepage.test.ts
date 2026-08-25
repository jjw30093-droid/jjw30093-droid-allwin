import { describe, expect, it } from "vitest";
import type { MatchSummary, WinProbability } from "@/lib/api-v1";
import type { HomeMatchCard } from "@/lib/homepage";
import {
  FEATURED_WINDOW_HOURS,
  marqueeRank,
  selectFeaturedMatch,
  selectHomepageEvidence,
  selectHomepageMatches,
} from "@/lib/homepage";

/**
 * `teamIds` 是 2026-08-19「强强对话优先」加的可选参数。既有用例一律不传,
 * 因此它们的 `home.team_id`/`away.team_id` 恒为 `undefined` —— 强强对话规则
 * 在旧用例上结构性不可能误触发(这也正是本次只有 1 个既有用例需要改写的原因)。
 * 注意 fixture 写的是 `home: { id }`(靠 `as MatchSummary` 绕过类型检查),
 * 那个 `id` 不是 `TeamRef.team_id`,选场逻辑从来不读它。
 */
function match(
  matchId: number,
  kickoff: string,
  probability: number | null = null,
  leagueId = 59,
  teamIds?: { home?: number; away?: number },
): HomeMatchCard {
  return {
    match: {
      match_id: matchId,
      league_id: leagueId,
      season: "2026",
      date_utc: kickoff.slice(0, 10),
      kickoff_at_utc: kickoff,
      status: "NotStarted",
      home: { id: matchId * 10, name: `主队${matchId}`, team_id: teamIds?.home },
      away: { id: matchId * 10 + 1, name: `客队${matchId}`, team_id: teamIds?.away },
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

  /**
   * N1(2026-08-19 改写,原用例名「有射门史的比赛顶掉时间更近但什么数据都没有的
   * 比赛」,断言 `featured === 2`)。
   *
   * **产品意图被本次需求有意推翻,不是测试被放松**(CLAUDE.md §17:既有测试
   * 不得为迁就实现而削弱,只有产品规则本身被推翻时才允许改写,并须留下推翻
   * 原因与日期)。原用例来自 2026-08-12「空页面永远不做重点」那一版:富集度是
   * 第一排序键,所以 +2d 的富数据比赛压过 +10min 的空壳比赛。
   *
   * 2026-08-19 站长把「重点比赛必须是 24 小时内的」定为硬门槛,富集度降级为
   * 窗口内的次级键 —— 同样这组输入,现在必须选窗口内那场空壳(match 1)。
   * 富集度并未失效:它仍决定窗口内多场比赛谁上重点位(见 N7 与既有 #8)。
   * 已知取舍(lib/homepage.ts::FEATURED_WINDOW_HOURS 注释同样如实记录):
   * 24h 内若全是空数据比赛,重点位仍会指向空页面。
   */
  it("N1:24 小时内的比赛优先于窗口外数据更全的比赛", () => {
    const emptySooner = match(1, "2026-08-12T12:10:00Z"); // 窗口内 +10min,零数据
    const richLater = match(2, "2026-08-14T12:00:00Z"); // 窗口外 +2d,有射门史

    const result = selectHomepageMatches([emptySooner, richLater], now, {
      withShots: new Set([2]),
    });

    expect(result.featured?.match.match_id).toBe(1);
    // 窗口外的富数据比赛没有被丢弃,只是让位:仍在 secondary 里可见。
    expect(result.secondary.map((c) => c.match.match_id)).toEqual([2]);
  });

  it("N2:24 小时内没有比赛时退化为最近开赛的一场", () => {
    // 全部落在窗口外 → 回退档按 |kickoff − now| 排序(精确保留 2026-08-12
    // 那版的语义),+30h 的那场比 +3d 的近。
    expect(FEATURED_WINDOW_HOURS).toBe(24); // 站长明确要求 24;改 48 是唯一逃生舱
    const farthest = match(11, "2026-08-15T12:00:00Z"); // +3d
    const nearest = match(12, "2026-08-13T18:00:00Z"); // +30h

    const result = selectHomepageMatches([farthest, nearest], now, {
      withShots: new Set(),
    });

    expect(result.featured?.match.match_id).toBe(12);
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

/**
 * 强强对话优先(2026-08-19)。名单是 FotMob team_id,不是队名字符串 ——
 * `TeamRef.name` 中文优先且随 i18n 覆盖变化,拿它做判据会在真实数据上漏判。
 */
describe("selectHomepageMatches:24 小时窗口内的强强对话优先", () => {
  const now = new Date("2026-08-12T12:00:00Z");
  const ARSENAL = 9825;
  const CHELSEA = 8455;
  const MAN_CITY = 8456;
  const MAN_UTD = 10260;
  const LIVERPOOL = 8650;
  const REAL_MADRID = 8633;
  const BARCELONA = 8634;
  const INTER = 8636;
  const JUVENTUS = 9885;
  const COVENTRY = 8669; // 考文垂:英超球队但不在 Big6 名单内(生产库实测 ID)

  /** 窗口内(+1h)、零数据、非强强对话的对照组。 */
  function ordinaryInWindow(matchId = 90): HomeMatchCard {
    return match(matchId, "2026-08-12T13:00:00Z");
  }

  it("N3:窗口内 Big6 内战优先于时间更近的普通比赛", () => {
    const derby = match(31, "2026-08-13T08:00:00Z", null, 47, {
      home: ARSENAL,
      away: CHELSEA,
    }); // +20h
    const result = selectHomepageMatches([ordinaryInWindow(), derby], now, {
      withShots: new Set(),
    });

    expect(result.featured?.match.match_id).toBe(31);
  });

  it("N4:窗口内皇马 vs 巴萨优先", () => {
    const clasico = match(41, "2026-08-13T08:00:00Z", null, 87, {
      home: REAL_MADRID,
      away: BARCELONA,
    });
    const result = selectHomepageMatches([ordinaryInWindow(), clasico], now, {
      withShots: new Set(),
    });

    expect(result.featured?.match.match_id).toBe(41);
  });

  it("N5:窗口内意甲三强互相对阵优先", () => {
    const derbyDItalia = match(51, "2026-08-13T08:00:00Z", null, 55, {
      home: INTER,
      away: JUVENTUS,
    });
    const result = selectHomepageMatches([ordinaryInWindow(), derbyDItalia], now, {
      withShots: new Set(),
    });

    expect(result.featured?.match.match_id).toBe(51);
  });

  it("N6:Big6 打非 Big6 不算强强对话(名单只认双方同组)", () => {
    const lopsided = match(61, "2026-08-13T08:00:00Z", null, 47, {
      home: ARSENAL,
      away: COVENTRY,
    }); // +20h
    const ordinary = ordinaryInWindow(62); // +1h
    const result = selectHomepageMatches([lopsided, ordinary], now, {
      withShots: new Set(),
    });

    expect(result.featured?.match.match_id).toBe(62);
    expect(marqueeRank(lopsided)).toBe(1);
  });

  it("N7:同为强强对话时按数据富集度再按时间", () => {
    const richLaterDerby = match(71, "2026-08-13T08:00:00Z", null, 47, {
      home: MAN_CITY,
      away: MAN_UTD,
    }); // +20h,有射门史
    const emptySoonerDerby = match(72, "2026-08-12T14:00:00Z", null, 47, {
      home: LIVERPOOL,
      away: CHELSEA,
    }); // +2h,零数据
    const result = selectHomepageMatches([emptySoonerDerby, richLaterDerby], now, {
      withShots: new Set([71]),
    });

    expect(result.featured?.match.match_id).toBe(71);
    expect(marqueeRank(richLaterDerby)).toBe(0);
    expect(marqueeRank(emptySoonerDerby)).toBe(0);
  });

  it("N8:team_id 缺失时不崩溃,也不被误判为强强对话", () => {
    // TeamRef.team_id 是 `number | null | undefined`(lib/api-types.ts),
    // 真实数据里未对齐的球队就是空值。
    const halfKnown = match(81, "2026-08-13T08:00:00Z", null, 47, { away: CHELSEA });
    const bothMissing = match(82, "2026-08-13T09:00:00Z", null, 47);
    const nulled = match(83, "2026-08-13T10:00:00Z", null, 47, {
      home: ARSENAL,
      away: CHELSEA,
    });
    (nulled.match.home as { team_id: number | null }).team_id = null;

    expect(marqueeRank(halfKnown)).toBe(1);
    expect(marqueeRank(bothMissing)).toBe(1);
    expect(marqueeRank(nulled)).toBe(1);

    const ordinary = ordinaryInWindow(84); // +1h
    expect(() =>
      selectHomepageMatches([halfKnown, bothMissing, nulled, ordinary], now, {
        withShots: new Set(),
      }),
    ).not.toThrow();
    const result = selectHomepageMatches([halfKnown, bothMissing, nulled, ordinary], now, {
      withShots: new Set(),
    });
    expect(result.featured?.match.match_id).toBe(84);
  });
});

describe("selectHomepageMatches(2026-08-12 改版:按开球时间就近排序)", () => {
  it("选中开球时间离当前最近的一场,已开球的排在所有未开赛之后", () => {
    const now = new Date("2026-08-12T12:00:00Z");
    const justKickedOff = match(1, "2026-08-12T11:30:00Z"); // 30min 前
    const soon = match(2, "2026-08-12T12:20:00Z"); // 20min 后(离 now 更近)
    const farAway = match(3, "2026-08-15T12:00:00Z");

    const result = selectHomepageMatches([justKickedOff, soon, farAway], now);

    expect(result.featured?.match.match_id).toBe(2);
    // 2026-08-19 站长要求「比赛一开始就放下一场」后,secondary 由 [1,3] 改为
    // [3,1]:已开球的 1 让位给 3 天后但仍未开赛的 3。featured 不受影响(仍是 2),
    // 变的只是已开球比赛在列表里的位次 —— 这是新规则的直接结果,不是放松断言。
    expect(result.secondary.map((c) => c.match.match_id)).toEqual([3, 1]);
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

/**
 * 2026-08-19 站长补充要求(原话):「没有重点比赛就放最近的比赛,然后比赛
 * 一开始就放下一场」。
 *
 * 前半句(没有则放最近)由 N2 与 L3 覆盖;后半句是本组的目标。
 *
 * ⚠️ 本组曾被实现成完全相反的语义:上一版加了一个"进行中/刚结束"粘滞档
 * (开球后 4 小时内一直占据重点位),并在注释里把它记成「站长要求比赛结束后
 * 2 小时才轮换」—— 那条要求站长从未提出过,是实现阶段自行加入的。站长实际
 * 要求的是**开球即轮换**,故整组用例连同该档一并推翻重写(2026-08-19),
 * 保留本段说明以免日后再被改回去。
 */
describe("重点比赛:比赛一开球就轮换到下一场", () => {
  const now = new Date("2026-08-12T12:00:00Z");

  function live(matchId: number, kickoff: string, status = "InPlay"): HomeMatchCard {
    const card = match(matchId, kickoff);
    (card.match as { status: string }).status = status;
    return card;
  }

  it("L1:比赛一开球就让位给下一场(哪怕还在进行中)", () => {
    const inPlay = live(1, "2026-08-12T11:00:00Z"); // 1 小时前开球,进行中
    const upcoming = match(2, "2026-08-12T13:00:00Z"); // 1 小时后开球

    const result = selectHomepageMatches([upcoming, inPlay], now);

    expect(result.featured?.match.match_id).toBe(2);
  });

  it("L2:已开球的比赛即使还没结束,也排在 24h 之外的未开赛比赛之后", () => {
    const inPlay = live(1, "2026-08-12T11:00:00Z"); // 进行中
    const farAway = match(2, "2026-08-15T12:00:00Z"); // 3 天后,已在 24h 窗口外

    const result = selectHomepageMatches([inPlay, farAway], now);

    // 未开赛恒优先于已开球:哪怕它远在 3 天后。
    expect(result.featured?.match.match_id).toBe(2);
  });

  it("L3:全部已开球时才轮到它们,并按离现在最近的一场", () => {
    const older = live(1, "2026-08-12T08:00:00Z", "Finish");
    const newer = live(2, "2026-08-12T11:00:00Z", "Finish");

    const result = selectHomepageMatches([older, newer], now);

    expect(result.featured?.match.match_id).toBe(2);
  });

  it("L4:强强对话一旦开球同样让位,不因是德比而赖着不走", () => {
    const derby = live(1, "2026-08-12T11:00:00Z");
    (derby.match.home as { team_id?: number }).team_id = 8633; // 皇马
    (derby.match.away as { team_id?: number }).team_id = 8634; // 巴萨
    const ordinary = match(2, "2026-08-12T13:00:00Z");

    const result = selectHomepageMatches([derby, ordinary], now);

    expect(result.featured?.match.match_id).toBe(2);
  });

  it("L5:全部已开球时,强强对话在该档内部仍然优先", () => {
    const ordinary = live(1, "2026-08-12T11:00:00Z");
    const derby = live(2, "2026-08-12T11:00:00Z");
    (derby.match.home as { team_id?: number }).team_id = 8633; // 皇马
    (derby.match.away as { team_id?: number }).team_id = 8634; // 巴萨

    const result = selectHomepageMatches([ordinary, derby], now);

    expect(result.featured?.match.match_id).toBe(2);
  });
});
