import type {
  GetJson,
  MatchSummary,
  TrackRecordResponse,
} from "@/lib/api-v1";
import type { FreeTip } from "@/components/matches/MatchRow";

export type AnalysisBundle = GetJson<"/api/v1/matches/{match_id}/analysis">;
export type AnalysisEvidence = AnalysisBundle["evidence"][number];

export type HomeMatchCard = {
  match: MatchSummary;
  tip: FreeTip | null;
};

function kickoffOf(card: HomeMatchCard): string {
  return card.match.kickoff_at_utc ?? card.match.date_utc;
}

/**
 * 联赛档位——只用于同一时间点附近多场比赛的打平判据,不是"谁更重要"的
 * 编辑判断。五大联赛+南美头部联赛的开球时段本身就天然错开(北京时间 EPL
 * 晚场约 23 点,西甲/意甲约 3 点,巴甲清晨约 5 点),按开球时间就近排序时
 * 自然会跟着这个节奏轮换;这张表只用来处理同一时段撞车的情况。
 */
const LEAGUE_TIER_RANK: Record<number, number> = {
  47: 0, // 英超
  87: 1, // 西甲
  55: 1, // 意甲
  54: 1, // 德甲
  53: 1, // 法甲
  268: 2, // 巴甲
};
function tierOf(card: HomeMatchCard): number {
  return LEAGUE_TIER_RANK[card.match.league_id] ?? 9;
}

/** 一场比赛"点进去有没有东西看"的真实判据。 */
export type MatchDataSignals = {
  /** 双方球队都有历史射门数据 → 赛前射门分布图画得出来(/matches?content=shots) */
  withShots: ReadonlySet<number>;
};

/**
 * 数据富集度打分:0 = 点进去基本空白,越大内容越厚。
 *
 * 射门图权重高于赔率,因为它是**视觉型**证据(一屏 200-300 个真实落点),
 * 而赔率目前每场只有 2-4 个观测点、只能显示两点变化条。
 *
 * `win_probability` 单独计分(而不是只依赖 `odds_coverage_tier`):重点位的
 * 核心卖点就是这条概率条(见 selectFeaturedMatch),一场已经算出概率的比赛
 * 即便赔率观测点还不足以定 tier,也应该比完全没有概率的比赛优先。
 */
export function dataRichness(card: HomeMatchCard, signals: MatchDataSignals): number {
  let score = 0;
  if (signals.withShots.has(card.match.match_id)) score += 2;
  const tier = card.match.odds_coverage_tier;
  if (tier === "full_timeline") score += 1;
  else if (tier === "open_close_only") score += 1;
  if (card.match.win_probability) score += 1;
  return score;
}

/**
 * 首页重点比赛的选择规则(2026-08-12 第二版:data-aware)。
 *
 * 第一版只按"开球时间与当前时刻的接近程度"排序,完全不看这场比赛有没有数据。
 * 实测后果(未来 7 天 78 场):**24 场(31%)射门与赔率都没有**;而本周赛程
 * 最多的四个联赛——英冠 12 / 巴甲 10 / 葡超 9 / 荷甲 9 共 40 场(51%)——
 * 在 dim_match 里 0 场完赛、0 行射门、0 行球队统计,是 2026-08-10 才接入的
 * 纯赛程壳。也就是说从短视频点进首页,约 1/3 概率重点卡指向一场什么都没有的
 * 比赛 —— 这对"用数据建立信任"的链路是直接反效果。
 *
 * 现在的排序键:
 *   1. 数据富集度(有射门史 / 有赔率)—— 空页面永远不做重点;
 *   2. 开球时间与当前时刻的接近程度(同富集度内,最近的一场自然成为重点);
 *   3. 联赛档位(同时段撞车时打平)。
 *
 * 注意富集度只做粗分档,不做连续排序 —— 否则会把一场三天后的英超顶到今晚
 * 开球的比赛前面,那同样不合理。
 *
 * 返回的新数组不会修改 API 原始顺序。
 */
export function selectHomepageMatches(
  cards: HomeMatchCard[],
  now: Date = new Date(),
  signals: MatchDataSignals = { withShots: new Set<number>() },
): {
  featured: HomeMatchCard | null;
  secondary: HomeMatchCard[];
  ordered: HomeMatchCard[];
} {
  const nowMs = now.getTime();
  const ordered = [...cards].sort((a, b) => {
    const ra = dataRichness(a, signals);
    const rb = dataRichness(b, signals);
    if (ra !== rb) return rb - ra;
    const da = Math.abs(new Date(kickoffOf(a)).getTime() - nowMs);
    const db = Math.abs(new Date(kickoffOf(b)).getTime() - nowMs);
    if (da !== db) return da - db;
    return tierOf(a) - tierOf(b);
  });

  return {
    featured: ordered[0] ?? null,
    secondary: ordered.slice(1),
    ordered,
  };
}

/**
 * 首页重点位选场(2026-08-16 权限口径修正:后端对任何人恒返回完整比赛内容,
 * `requires_login` 字段已从 MatchSummary 彻底删除——"一免费一锁定对照卡"
 * 这个产品概念随之消失,不存在需要"诚实展示这场看不到概率"的锁定比赛了)。
 *
 * 取代此前的 `selectHeroPair`(freeCard/lockedCard 双卡对照)。新设计:
 * - `featured`:复用 `selectHomepageMatches` 的数据富集度排序(优先选真的
 *   有射门史/赔率、因而算得出 `win_probability` 的比赛;同富集度按开球
 *   时间就近、再按联赛档位打平)——重点位的核心卖点是这条概率条,不看
 *   身份,只看这场比赛本身是否有数据。
 * - `secondary`:按开球时间顺序的剩余比赛,已排除 `featured`,避免同一场
 *   比赛在重点位和"近期比赛"横滑区里重复出现。
 * - 候选池为空时 `featured` 为 `null`,调用方回退到"暂无已排期的未来比赛"
 *   空态,不臆造数据。
 */
export function selectFeaturedMatch(
  cards: HomeMatchCard[],
  now: Date = new Date(),
  signals: MatchDataSignals = { withShots: new Set<number>() },
): {
  featured: HomeMatchCard | null;
  secondary: HomeMatchCard[];
} {
  const { featured } = selectHomepageMatches(cards, now, signals);
  const byKickoff = [...cards].sort((a, b) =>
    kickoffOf(a).localeCompare(kickoffOf(b)),
  );
  const secondary = byKickoff.filter(
    (c) => c.match.match_id !== featured?.match.match_id,
  );
  return { featured, secondary };
}

/**
 * 首屏只显示真实存在的三类证据,每类最多一条。
 * 缺少某类时直接减少条数,不使用其他字段或模拟文案补位。
 */
export function selectHomepageEvidence(
  evidence: AnalysisEvidence[] | null | undefined,
): AnalysisEvidence[] {
  if (!evidence?.length) return [];
  return ["form", "season_xg", "rest"]
    .map((kind) => evidence.find((item) => item.kind === kind))
    .filter((item): item is AnalysisEvidence => Boolean(item));
}

export type PublicRecordView =
  | { status: "error" }
  | { status: "empty" }
  | {
      status: "ready";
      total: number;
      evaluated: number;
      accuracy: number | null;
      samples: TrackRecordResponse["samples"];
    };

export function publicRecordView(
  data: TrackRecordResponse | null,
  failed = false,
): PublicRecordView {
  if (failed || !data) return { status: "error" };
  if (data.total <= 0) return { status: "empty" };
  return {
    status: "ready",
    total: data.total,
    evaluated: data.metrics?.sample_size ?? 0,
    accuracy:
      typeof data.metrics?.accuracy === "number"
        ? data.metrics.accuracy
        : null,
    samples: data.samples,
  };
}
