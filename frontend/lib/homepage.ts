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

/**
 * 重点比赛的时间窗口(小时)——**站长明确要求 24**,是硬门槛不是加权项。
 *
 * 已知取舍(按站长原话实现,如实记录,不做隐藏放宽):
 * ① 周六上午访问时,周日晚的 Big6 内战(约 30h 后)进不了重点位;
 * ② 若某个周中 24h 内只有英冠/荷甲这类空数据比赛,重点位会指向空页面 ——
 *    这正是 2026-08-12 那版要解决的问题。窗口内按 `dataRichness` 排序能缓解
 *    (有数据的排前面),但窗口内**全部**无数据时无法避免。
 *
 * 逃生舱就是这一个常量:改成 48 即可让周末的强强对话够得着。
 */
export const FEATURED_WINDOW_HOURS = 24;

const FEATURED_WINDOW_MS = FEATURED_WINDOW_HOURS * 60 * 60 * 1000;

/**
 * 强强对话名单(FotMob `team_id`,已在生产库逐一核对)。
 *
 * **这与上面 LEAGUE_TIER_RANK 注释里"不是'谁更重要'的编辑判断"那句话方向相反,
 * 是有意的**:2026-08-19 站长要求"周末比赛多时强强对话优先",本表就是一份
 * 编辑名单、一次明确的编辑判断,不是从数据里算出来的客观档位。二者并存:
 * 联赛档位仍然只做最末打平键,强强对话是窗口内的首要键。
 *
 * 判定规则是**两队同属同一组**才算(Big6 打保级队不算)。三组同级,不再细分先后。
 * 结构是 `readonly (readonly number[])[]`,**加一组就是加一行**。
 *
 * 可选扩展(本次不启用,站长随时可开):
 *   [8633, 9906]              // 马德里德比:皇马 · 马竞
 *   [9823, 9789]              // 德国国家德比:拜仁 · 多特
 *   意甲组并入 9875 那不勒斯 / 8686 罗马
 * 欧冠(league 42)库里标"暂未收录",现在加进来不会生效。
 */
const MARQUEE_GROUPS: readonly (readonly number[])[] = [
  // 英超 Big6:曼城 · 曼联 · 利物浦 · 切尔西 · 阿森纳 · 热刺
  [8456, 10260, 8650, 8455, 9825, 8586],
  // 西甲国家德比:皇马 · 巴萨
  [8633, 8634],
  // 意甲三强:AC米兰 · 国米 · 尤文
  [8564, 8636, 9885],
];

/**
 * 强强对话档位:0 = 双方同属名单里的同一组;1 = 其余。
 *
 * `TeamRef.team_id` 在生成类型里是 `number | null | undefined`(非必填),真实
 * 数据里未对齐的球队就是空值,必须做空值保护 —— 空值一律归入 1,不臆测。
 *
 * **禁止用队名字符串匹配**:`TeamRef.name` 是中文优先且随 i18n 覆盖变化,
 * 拿它做判据会在真实数据上漏判。
 */
export function marqueeRank(card: HomeMatchCard): number {
  const home = card.match.home?.team_id;
  const away = card.match.away?.team_id;
  if (typeof home !== "number" || typeof away !== "number") return 1;
  const sameGroup = MARQUEE_GROUPS.some(
    (group) => group.includes(home) && group.includes(away),
  );
  return sameGroup ? 0 : 1;
}

function kickoffMsOf(card: HomeMatchCard): number {
  return new Date(kickoffOf(card)).getTime();
}

/**
 * 是否落在重点位窗口内:开球时刻 ∈ (now, now + FEATURED_WINDOW_HOURS]。
 *
 * 右边界取**闭区间**:恰好 24h 后开球的比赛算在窗口内。
 * 左边界取**开区间**:已开球的比赛不进这一档 —— 见 isUpcoming,它们排在
 * 所有未开赛比赛之后(站长要求「比赛一开始就放下一场」)。
 */
function inFeaturedWindow(card: HomeMatchCard, nowMs: number): boolean {
  const ahead = kickoffMsOf(card) - nowMs;
  return ahead > 0 && ahead <= FEATURED_WINDOW_MS;
}

/**
 * 是否尚未开球。站长要求(2026-08-19):「比赛一开始就放下一场」——
 * 重点位讲的是"接下来该看哪场",开球那一刻就该轮换到下一场,而不是
 * 继续占着位置。因此已开球的比赛(含进行中、已完赛)一律排在**所有**
 * 未开赛比赛之后,只有在候选池里一场未开赛的比赛都没有时才会露面。
 */
function isUpcoming(card: HomeMatchCard, nowMs: number): boolean {
  return kickoffMsOf(card) - nowMs > 0;
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
 * 首页重点比赛的选择规则(2026-08-19 第三版:24 小时窗口 + 强强对话优先)。
 *
 * 演进史(改这里之前先读完,免得把上一版解决的问题又改回来):
 *
 * - 第一版只按"开球时间与当前时刻的接近程度"排序,完全不看有没有数据。实测
 *   后果(未来 7 天 78 场):24 场(31%)射门与赔率都没有;赛程最多的四个联赛
 *   (英冠/巴甲/葡超/荷甲 共 40 场)在 dim_match 里 0 场完赛、0 行射门 ——
 *   约 1/3 概率重点卡指向一场空白比赛。
 * - 第二版(2026-08-12)把**数据富集度提为第一键**修掉了上面这条,代价是
 *   一场三天后的富数据比赛会顶掉今晚开球的比赛。
 * - 第三版(2026-08-19,站长要求)把"重点比赛必须是 24 小时内的"定为硬门槛,
 *   富集度降级为窗口内的次级键;周末比赛多时,窗口内的**强强对话优先**。
 *
 * 现在的排序键(三档,依次比较):
 *   档 A  未开赛 且 在 24h 窗口内 → 强强对话 → 数据富集度 → 开球就近 → 联赛档位
 *   档 B  未开赛 但 在 24h 之外   → 开球就近 → 强强对话 → 数据富集度 → 联赛档位
 *   档 C  已开球(进行中/已完赛)  → |开球时间 − now| → 强强对话 → 富集度 → 档位
 *
 * 档 A/B 的分界就是站长要的「24 小时」硬门槛;A、B 都为空时才轮到 C。
 * 「比赛一开始就放下一场」由 A、B 恒排在 C 之前来保证:只要候选池里还有
 * **任何**一场未开赛的比赛,重点位就不会是一场已经开球的比赛。
 * 档 B 内部按开球就近排序,对应站长的「没有重点比赛就放最近的比赛」。
 *
 * 富集度依旧只做粗分档、不做连续排序。
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
    // 主键 0:未开赛的一律排在已开球的之前(站长:「比赛一开始就放下一场」)。
    const aUp = isUpcoming(a, nowMs);
    const bUp = isUpcoming(b, nowMs);
    if (aUp !== bUp) return aUp ? -1 : 1;

    // 主键 1:未开赛内部再按是否落在 24h 窗口分档。
    const aIn = inFeaturedWindow(a, nowMs);
    const bIn = inFeaturedWindow(b, nowMs);
    if (aUp && aIn !== bIn) return aIn ? -1 : 1;

    const ma = marqueeRank(a);
    const mb = marqueeRank(b);
    const ra = dataRichness(a, signals);
    const rb = dataRichness(b, signals);

    if (aUp && aIn) {
      // 档 A(24h 窗口内):强强对话 → 富集度 → 开球就近 → 联赛档位
      if (ma !== mb) return ma - mb;
      if (ra !== rb) return rb - ra;
      const ahead = kickoffMsOf(a) - kickoffMsOf(b);
      if (ahead !== 0) return ahead;
      return tierOf(a) - tierOf(b);
    }

    if (aUp) {
      // 档 B(未开赛、24h 之外):先按开球就近 —— 站长的「没有重点比赛就放
      // 最近的比赛」。强强对话不在这里越级抢位,否则等于变相放宽 24h 门槛。
      const ahead = kickoffMsOf(a) - kickoffMsOf(b);
      if (ahead !== 0) return ahead;
      if (ma !== mb) return ma - mb;
      if (ra !== rb) return rb - ra;
      return tierOf(a) - tierOf(b);
    }

    // 档 C(已开球):只有候选池里一场未开赛的都没有时才会成为重点位。
    const da = Math.abs(kickoffMsOf(a) - nowMs);
    const db = Math.abs(kickoffMsOf(b) - nowMs);
    if (da !== db) return da - db;
    if (ma !== mb) return ma - mb;
    if (ra !== rb) return rb - ra;
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
 * - `featured`:直接复用 `selectHomepageMatches` 的排序(2026-08-19 起为
 *   24h 窗口 → 强强对话 → 数据富集度 → 开球时间 → 联赛档位,见该函数注释)
 *   ——不看身份,只看这场比赛本身在什么时候踢、是不是强强对话、有没有数据。
 *   这里**不额外加 `win_probability` 过滤**:候选池扩大后横滑区确实会混入
 *   无概率的比赛,但 HomeMatchExperienceLive 的 `暂无赔率数据` 兜底已经诚实
 *   处理了这种情况;加过滤反而会在"整池都没有概率"时把 secondary 清空。
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
