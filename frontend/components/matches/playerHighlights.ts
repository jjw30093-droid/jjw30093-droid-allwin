/**
 * 球员本场亮点句(2026-09-10,对照 FotMob 官方安卓包 236.17398)。
 *
 * FotMob 的做法(反编译实证):
 * `com.fotmob.android.feature.match.ui.matchplayerstats.model.PlayerHighlight`
 * 有 POSITIVE/NEGATIVE 两种 type、一个 priority、一个 rankableCategory,把该
 * 球员在**本场全部球员**里按某个类目排名,命中就生成一句文案。文案模板在
 * resources 里有四个变体:现在时/过去时 × 独占/并列("Has the most touches"
 * / "Had the joint-most touches")。所以这套东西 100% 由"本场这批球员的同一
 * 批统计字段"算出来,不需要任何额外数据 —— 我们已经全量下发,前端就能算。
 *
 * 本文件**刻意不带 "use client"**:虽然当前唯一调用方 MatchStatsSection 是
 * 客户端组件,但 CLAUDE.md §11.4 那次真实白屏事故的根因就是"纯函数住在
 * client 文件里,被服务端组件 import 后线上才炸,next build 抓不到"。纯逻辑
 * 单独成文件,以后谁在服务端组件里用它都安全。
 *
 * 两条口径纪律:
 * 1. 缺失值一律不参与排名(CLAUDE.md §11.3:不得静默当 0)。体能类字段只有
 *    部分联赛有,全场只有一两个人有值时不能让他"夺冠"——所以排名类目要求
 *    池子里至少 MIN_POOL 个人有值。
 * 2. 事件型字段(击中门框/送点/门线解围/失误导致丢球…)在来源 payload 里
 *    只有真发生时才出现,池子天然小于 MIN_POOL,排名机制对它们结构性失效。
 *    这类按"发生即播报"处理,而且**只陈述事实、不声称排名**。
 */

import type { MatchReportResponse } from "@/lib/api-v1";

type MatchReport = Extract<MatchReportResponse, { available: true }>;
export type PlayerStat = MatchReport["player_stats"][number];

export type HighlightTone = "positive" | "negative";
export type PlayerHighlight = { key: string; text: string; tone: HighlightTone };

/** 排名类目至少要有这么多人有值,"全场最多"才算一句有信息量的话。 */
export const MIN_POOL = 6;
const DEFAULT_MAX = 3;

type NumericKey = {
  [K in keyof PlayerStat]-?: NonNullable<PlayerStat[K]> extends number ? K : never;
}[keyof PlayerStat];

type RankCategory = {
  key: NumericKey;
  label: string;
  tone: HighlightTone;
  /** 越小越靠前。 */
  priority: number;
  /** 低于这个值就算"全场最多"也不值一提(如"全场犯规最多(1)")。 */
  min: number;
  format?: (v: number) => string;
};

type EventCategory = {
  key: NumericKey;
  label: string;
  tone: HighlightTone;
  priority: number;
  min: number;
  /** true=只播报"发生了",不带次数(次数为 1 时更自然)。 */
  countable?: boolean;
};

const km = (v: number) => `${(v / 1000).toFixed(1)} 公里`;
const kmh = (v: number) => `${v.toFixed(1)} km/h`;
const dp2 = (v: number) => v.toFixed(2);
const int = (v: number) => String(Math.round(v));

/** 排名类目(值越大越突出)。 */
const RANK_CATEGORIES: RankCategory[] = [
  { key: "chances_created", label: "创造机会", tone: "positive", priority: 10, min: 2 },
  { key: "expected_goals", label: "xG", tone: "positive", priority: 12, min: 0.3, format: dp2 },
  { key: "shots_on_target", label: "射正", tone: "positive", priority: 14, min: 2 },
  { key: "dribbles_succeeded", label: "成功过人", tone: "positive", priority: 16, min: 2 },
  { key: "touches_opp_box", label: "对方禁区触球", tone: "positive", priority: 20, min: 3 },
  { key: "tackles", label: "抢断", tone: "positive", priority: 22, min: 3 },
  { key: "interceptions", label: "拦截", tone: "positive", priority: 23, min: 3 },
  { key: "defensive_actions", label: "防守行动", tone: "positive", priority: 25, min: 5 },
  { key: "duel_won", label: "对抗成功", tone: "positive", priority: 26, min: 4 },
  { key: "aerials_won", label: "争顶成功", tone: "positive", priority: 27, min: 3 },
  { key: "recoveries", label: "回追", tone: "positive", priority: 28, min: 5 },
  { key: "touches", label: "触球", tone: "positive", priority: 30, min: 20 },
  { key: "accurate_passes", label: "成功传球", tone: "positive", priority: 32, min: 20 },
  {
    key: "physical_metrics_topspeed",
    label: "最高速度", tone: "positive", priority: 34, min: 25, format: kmh,
  },
  {
    key: "physical_metrics_distance_covered",
    label: "跑动距离", tone: "positive", priority: 35, min: 5000, format: km,
  },
  { key: "big_chance_missed", label: "错失绝佳机会", tone: "negative", priority: 45, min: 2 },
  { key: "dispossessed", label: "丢球", tone: "negative", priority: 50, min: 4 },
  { key: "dribbled_past", label: "被过人", tone: "negative", priority: 52, min: 3 },
  { key: "fouls", label: "犯规", tone: "negative", priority: 54, min: 3 },
];

/** 事件类目:发生即播报,不声称排名(来源只发非零项,池子永远凑不够)。 */
const EVENT_CATEGORIES: EventCategory[] = [
  { key: "own_goals", label: "乌龙球", tone: "negative", priority: 1, min: 1, countable: true },
  { key: "errors_led_to_goal", label: "失误导致丢球", tone: "negative", priority: 2, min: 1, countable: true },
  { key: "missed_penalty", label: "射失点球", tone: "negative", priority: 3, min: 1, countable: true },
  { key: "saved_penalties", label: "扑出点球", tone: "positive", priority: 4, min: 1, countable: true },
  { key: "penalties_won", label: "赢得点球", tone: "positive", priority: 5, min: 1, countable: true },
  { key: "conceded_penalties", label: "送点", tone: "negative", priority: 6, min: 1, countable: true },
  { key: "clearance_off_the_line", label: "门线解围", tone: "positive", priority: 7, min: 1, countable: true },
  { key: "last_man_tackle", label: "最后一人抢断", tone: "positive", priority: 8, min: 1, countable: true },
  { key: "shots_woodwork", label: "击中门框", tone: "negative", priority: 9, min: 1, countable: true },
];

function numeric(p: PlayerStat, key: NumericKey): number | null {
  const v = (p as Record<string, unknown>)[key];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function buildPlayerHighlights(
  player: PlayerStat,
  all: readonly PlayerStat[],
  opts: { finished?: boolean; max?: number } = {}
): PlayerHighlight[] {
  const finished = opts.finished ?? true;
  // 比赛还在进行时说"全场最多"是在替一场没踢完的比赛下结论
  const scope = finished ? "全场" : "目前";
  const found: (PlayerHighlight & { priority: number })[] = [];

  for (const cat of RANK_CATEGORIES) {
    const mine = numeric(player, cat.key);
    if (mine == null || mine < cat.min) continue;
    const pool: number[] = [];
    for (const p of all) {
      const v = numeric(p, cat.key);
      if (v != null) pool.push(v);
    }
    if (pool.length < MIN_POOL) continue;
    const best = Math.max(...pool);
    if (mine < best) continue;
    const joint = pool.filter((v) => v === best).length > 1;
    const shown = (cat.format ?? int)(mine);
    found.push({
      key: String(cat.key),
      tone: cat.tone,
      priority: cat.priority,
      text: `${scope}${cat.label}${joint ? "并列" : ""}最多（${shown}）`,
    });
  }

  for (const cat of EVENT_CATEGORIES) {
    const mine = numeric(player, cat.key);
    if (mine == null || mine < cat.min) continue;
    const n = Math.round(mine);
    found.push({
      key: String(cat.key),
      tone: cat.tone,
      priority: cat.priority,
      text: cat.countable && n > 1 ? `${cat.label} ${n} 次` : cat.label,
    });
  }

  found.sort((a, b) => a.priority - b.priority);
  return found
    .slice(0, opts.max ?? DEFAULT_MAX)
    .map(({ key, text, tone }) => ({ key, text, tone }));
}

/** 该球员本场射门摘要。
 *
 * 射门数与 xG 合计取自射门图同一份数据(report.shots 按 player_id 过滤),
 * **射正数则取官方球员统计**(player.shots_on_target),两者刻意不同源。
 *
 * 为什么射正不从射门图逐脚推(2026-09-10 生产库实测,不是设计洁癖):
 * - 逐脚推的口径是"进球 + 未被封堵的 AttemptSaved"(ShotMapChart 里那个
 *   isOnTarget),它依赖 fact_shotmap.Is_Blocked;
 * - 但生产库 372,445 脚射门里 Is_Blocked 只有 56,634 脚(15.2%)非空;
 * - 按(球员,比赛)对官方 ShotsOnTarget 校验:Is_Blocked 已回填的 30,889 例
 *   一致率 99.5%,**未回填的 175,040 例只有 61.1%**(AttemptSaved 混入被后卫
 *   封堵的球,系统性高估;队级平均每队每场多算 3.37 脚)。也就是说逐脚推的
 *   射正数在大多数比赛里会给用户一个错的数字——当图上的形状/筛选没问题,
 *   当一个报出来的数值不行;
 * - 官方值覆盖全部射手,且实测 205,929 个(球员,比赛)样本中**从未**超过射门图
 *   的射门数(0.00%),不会出现"射门 2 次 · 射正 3"这种自相矛盾。
 *
 * 官方值缺失时 onTarget 为 null,该项整段不显示——不退回那个已知有偏的推算。
 */
export type ShotSummary = {
  shots: number;
  /** 官方射正数;缺失时为 null(不推算、不当 0)。 */
  onTarget: number | null;
  /** xG 合计;有射门但全部缺 xG 时为 null —— 缺失值不当 0 累加(§11.3)。 */
  xgTotal: number | null;
  /** 参与 xG 合计的射门数,小于 shots 时说明部分射门没有 xG。 */
  xgCounted: number;
};

export function summarizePlayerShots(
  shots: readonly MatchReport["shots"][number][],
  player: PlayerStat
): ShotSummary | null {
  const mine = shots.filter((s) => String(s.player_id) === String(player.player_id));
  if (mine.length === 0) return null;
  let xgTotal = 0;
  let xgCounted = 0;
  for (const s of mine) {
    if (typeof s.xg === "number" && Number.isFinite(s.xg)) {
      xgTotal += s.xg;
      xgCounted += 1;
    }
  }
  const official = player.shots_on_target;
  return {
    shots: mine.length,
    onTarget:
      typeof official === "number" && Number.isFinite(official)
        ? Math.round(official)
        : null,
    xgTotal: xgCounted > 0 ? xgTotal : null,
    xgCounted,
  };
}
