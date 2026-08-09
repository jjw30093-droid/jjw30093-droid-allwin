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
 * 2026-08-07 J 联赛跨年新赛季首轮开幕的一次性首页编排。
 *
 * 这是明确指定的一次性活动置顶,不是通用"运营置顶"框架——CLAUDE.md 不为
 * "以后可能用到"提前抽象,过期后这段常量与 selectFeaturedOverride 应直接
 * 删除,不要沉淀成常驻配置项。
 *
 * 规则:首轮最早开球的横滨水手 vs 鹿岛鹿角(match_id 5803519,
 * 2026-08-07T10:25:00Z,数据库真实查询得出的该轮最早开球场次)置顶为首页
 * 热门;开球 2 小时后自动切换为瑞典超(67)/挪威超(59)当时离现在最近的
 * 一场比赛(不区分未来还是刚开球/进行中,单纯按 |kickoff-now| 最小)。
 */
export const HOMEPAGE_FEATURE_EVENT = {
  pinnedMatchId: 5803519,
  pinnedKickoffUtc: "2026-08-07T10:25:00Z",
  pinWindowHours: 2,
  fallbackLeagueIds: [67, 59] as const,
};

/**
 * 覆盖规则的选择结果;null 表示不适用覆盖,调用方应回退到
 * selectHomepageMatches 的默认结果。
 */
export function selectFeaturedOverride(
  candidates: HomeMatchCard[],
  nordicCandidates: HomeMatchCard[],
  now: Date,
): HomeMatchCard | null {
  const pinEndMs =
    new Date(HOMEPAGE_FEATURE_EVENT.pinnedKickoffUtc).getTime() +
    HOMEPAGE_FEATURE_EVENT.pinWindowHours * 3600_000;

  if (now.getTime() < pinEndMs) {
    const pinned = candidates.find(
      (c) => c.match.match_id === HOMEPAGE_FEATURE_EVENT.pinnedMatchId,
    );
    if (pinned) return pinned;
    // 置顶场次这段时间理应总能取到;取不到时不伪造,直接落到下面的
    // 北欧回退或最终的默认算法,不在这里报错阻断首页渲染。
  }

  if (nordicCandidates.length === 0) return null;
  const nowMs = now.getTime();
  return [...nordicCandidates].sort((a, b) => {
    const da = Math.abs(new Date(kickoffOf(a)).getTime() - nowMs);
    const db = Math.abs(new Date(kickoffOf(b)).getTime() - nowMs);
    return da - db;
  })[0];
}

function hasValidPublicTip(card: HomeMatchCard): boolean {
  const probability = card.tip?.top_probability;
  return (
    typeof probability === "number" &&
    Number.isFinite(probability) &&
    probability >= 0 &&
    probability <= 1
  );
}

/**
 * 首页重点比赛的唯一选择规则:
 * 先选具有合法匿名公开概率的最近比赛;没有公开概率时退化到最近开球比赛。
 * 返回的新数组不会修改 API 原始顺序。
 */
export function selectHomepageMatches(cards: HomeMatchCard[]): {
  featured: HomeMatchCard | null;
  secondary: HomeMatchCard[];
  ordered: HomeMatchCard[];
} {
  const ordered = [...cards].sort((a, b) => {
    const predictionPriority =
      Number(hasValidPublicTip(b)) - Number(hasValidPublicTip(a));
    if (predictionPriority !== 0) return predictionPriority;
    return kickoffOf(a).localeCompare(kickoffOf(b));
  });

  return {
    featured: ordered[0] ?? null,
    secondary: ordered.slice(1),
    ordered,
  };
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
