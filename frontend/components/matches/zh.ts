/** 比赛相关页面共用的中文映射与格式化(不含任何请求逻辑)。 */

export const OUTCOME_ZH: Record<"home" | "draw" | "away", string> = {
  home: "主胜",
  draw: "平局",
  away: "客胜",
};

export const STATUS_ZH: Record<string, string> = {
  NotStarted: "未开赛",
  InPlay: "进行中",
  Finish: "已完赛",
};

export const PRED_STATUS_ZH: Record<string, string> = {
  published: "已发布",
  locked: "已锁定",
  retracted: "已撤回",
};

export const MARKET_ZH: Record<string, string> = {
  "1x2": "胜平负(欧赔)",
  ah: "亚洲让球",
  ou: "大小球",
};

/** bronze_legacy_odds_summary.source——历史存档赔率的批次来源(与
 * backend/migrations/odds/0004_legacy_odds_summary.sql /
 * 0005_legacy_source_jka.sql 的 CHECK 约束对齐)。同一公司出现两行不同
 * 数字时,标出批次来源比默默去重更诚实——两个批次本就可能是独立抓取,
 * 数值不同是真实的数据差异,不是渲染 bug。 */
export const LEGACY_SOURCE_ZH: Record<string, string> = {
  asset_a_json: "存档 A",
  asset_b_footballdata: "存档 B·football-data",
  asset_b_nowgoal: "存档 B·NowGoal",
  football_uk_jka: "旧库 J/K/澳",
  nowgoal_archive_refetch: "NowGoal 重抓",
};

/** 各市场 payload 字段 → 中文列名(与 backend/providers/nowgoal.py _FIELD_MAP 对齐) */
export const MARKET_FIELDS: Record<string, { key: string; label: string }[]> = {
  "1x2": [
    { key: "home", label: "主胜" },
    { key: "draw", label: "平局" },
    { key: "away", label: "客胜" },
  ],
  ah: [
    { key: "home", label: "主队" },
    { key: "line", label: "盘口线" },
    { key: "away", label: "客队" },
  ],
  ou: [
    { key: "over", label: "大球" },
    { key: "line", label: "盘口线" },
    { key: "under", label: "小球" },
  ],
};

export const EVENT_TYPE_ZH: Record<string, string> = {
  lineup_change: "阵容变化",
  sideline_change: "伤停名单变化",
};

/* ── 完赛事实报告(/matches/{id}/report 四 tab)词表 ────────────────────
 * 与上面的 EVENT_TYPE_ZH(赛前阵容/伤停"同期事件")是两套不同概念,不合并。
 * 全部枚举值来自真实库 DISTINCT 核对(2026-08);来源新增值兜底显示原文。 */

export const POSITION_ZH: Record<string, string> = {
  GK: "门将",
  DEF: "后卫",
  MID: "中场",
  FWD: "前锋",
};

export const MATCH_EVENT_ZH: Record<string, string> = {
  Goal: "进球",
  Card: "牌",
  Substitution: "换人",
  Half: "半场",
  AddedTime: "补时",
  VAR: "VAR 判罚",
  MissedPenalty: "射失点球",
  PenaltyShootout: "点球大战",
  Comment: "说明",
};

export const CARD_ZH: Record<string, string> = {
  Yellow: "黄牌",
  Red: "红牌",
  YellowRed: "两黄变红",
};

export const SHOT_OUTCOME_ZH: Record<string, string> = {
  Goal: "进球",
  AttemptSaved: "被扑出",
  Miss: "偏出",
  Post: "中框",
};

export const SHOT_SITUATION_ZH: Record<string, string> = {
  RegularPlay: "运动战",
  FastBreak: "快速反击",
  SetPiece: "定位球",
  FromCorner: "角球",
  FreeKick: "任意球",
  Penalty: "点球",
  ThrowInSetPiece: "界外球战术",
  IndividualPlay: "个人突破",
};

export const SHOT_TYPE_ZH: Record<string, string> = {
  RightFoot: "右脚",
  LeftFoot: "左脚",
  Header: "头球",
  OtherBodyParts: "其他部位",
};

/** 球队数据对比行(顺序即展示顺序;format 决定数值渲染方式)。
 * key 与 MatchReportTeamStat 的字段名一一对应(Pydantic 单一真源生成)。 */
export const TEAM_STAT_LABELS: { key: string; label: string; format: "pct" | "num" | "num1" }[] = [
  { key: "possession", label: "控球率", format: "pct" },
  // "官方统计 xG"取自 FotMob 团队统计接口,与统计 tab 下方射门图自行按
  // shots[] 求和的"射门图 xG 合计"是两个独立来源,分别命名(不同名混称
  // 会让用户误以为数值不一致是 bug)。
  { key: "expected_goals", label: "官方统计 xG", format: "num1" },
  { key: "total_shots", label: "射门", format: "num" },
  { key: "shots_on_target", label: "射正", format: "num" },
  { key: "big_chance", label: "绝佳机会", format: "num" },
  { key: "shots_inside_box", label: "禁区内射门", format: "num" },
  { key: "touches_opp_box", label: "对方禁区触球", format: "num" },
  { key: "accurate_passes", label: "成功传球", format: "num" },
  { key: "accurate_crosses", label: "成功传中", format: "num" },
  { key: "corners", label: "角球", format: "num" },
  { key: "tackles", label: "抢断", format: "num" },
  { key: "interceptions", label: "拦截", format: "num" },
  { key: "clearances", label: "解围", format: "num" },
  { key: "keeper_saves", label: "门将扑救", format: "num" },
  { key: "duel_won", label: "对抗成功", format: "num" },
  { key: "aerials_won", label: "争顶成功", format: "num" },
  { key: "fouls", label: "犯规", format: "num" },
  { key: "offsides", label: "越位", format: "num" },
  { key: "yellow_cards", label: "黄牌", format: "num" },
  { key: "red_cards", label: "红牌", format: "num" },
];

/** 联赛静态元数据(镜像 backend/queries/leagues.py LEAGUE_META,仅作显示回退) */
export const LEAGUE_ZH: Record<number, string> = {
  47: "英超",
  87: "西甲",
  55: "意甲",
  54: "德甲",
  53: "法甲",
  59: "挪威超",
  67: "瑞典超",
  223: "日职联",
  9080: "韩K联",
  113: "澳超",
  48: "英冠",
  57: "荷甲",
  61: "葡超",
  268: "巴甲",
  42: "欧冠",
  73: "欧联",
  10216: "欧协联",
};

export const ENTITLEMENT_ZH: Record<string, string> = {
  "league:epl": "英超联赛数据",
  "league:lottery": "竞彩常见联赛数据(欧冠欧联/五大联赛外的英冠荷甲葡超巴甲/日韩澳/北欧)",
  "league:top5": "五大联赛数据",
  "prediction:top_probability": "模型最高一项概率",
  "prediction:full_wdl": "完整胜平负三项概率",
  "prediction:score_matrix": "比分概率矩阵",
  "report:deep": "深度报告与同期事件明细",
  "odds:summary_delayed": "赔率摘要(延迟约 1 小时)",
  "odds:history_full": "完整赔率时间线",
  "odds:raw": "原始赔率快照",
  "export:basic": "基础导出",
  "export:full": "完整导出",
  "alert:odds": "赔率变化提醒",
};

export function entitlementLabel(code: string): string {
  return ENTITLEMENT_ZH[code] ?? code;
}

export function pct(v: number): string {
  return `${Math.round(v * 100)}%`;
}

/** "2026-07-19" → "2026年7月19日"(比赛日只精确到日,UTC 口径,不做时区换算) */
export function formatDateZh(dateStr: string): string {
  const parts = dateStr.split("-");
  if (parts.length !== 3) return dateStr;
  const [y, m, d] = parts;
  return `${y}年${parseInt(m, 10)}月${parseInt(d, 10)}日`;
}

export function formatPrice(cents: number, currency: string): string {
  const amount = (cents / 100).toFixed(cents % 100 === 0 ? 0 : 2);
  if (currency === "CNY" || currency === "RMB") return `¥${amount}`;
  return `${amount} ${currency}`;
}

/* ── 北京时间(UTC+8,固定无夏令时)展示 ──────────────────────────────
 * 中文足球用户默认按北京时间找比赛(CLAUDE.md §11.2)。北京时间是固定
 * UTC+8 偏移,纯算术换算即可,不依赖 Intl/ICU 时区数据库,任何运行时都
 * 不会出错(不像 LocalTime.tsx 的浏览器本地时区那样需要水合后才能定)。
 *
 * 绝不对 date_only 值(如 "2026-08-21",只精确到日、没有 kickoff 时刻)
 * 做换算——那样会凭空捏造一个从未来源提供过的具体时刻,误导用户去
 * 错误的钟点蹲比赛(CLAUDE.md §6.2.1)。所有函数对 date_only/非法输入
 * 统一返回 null,调用方必须显式处理"没有精确时刻"这个分支,不能假装
 * 总能拿到一个时间。 */

const DATE_ONLY_RE = /^\d{4}-\d{2}-\d{2}$/;
const HAS_TZ_RE = /[Zz]|[+-]\d{2}:?\d{2}$/;
const BEIJING_OFFSET_MS = 8 * 60 * 60 * 1000;

/** 精确 UTC 时刻(含时分)→ 毫秒;date_only 或无法解析一律 null,不猜测。 */
function toExactEpochMs(iso: string): number | null {
  if (DATE_ONLY_RE.test(iso)) return null;
  const normalized = HAS_TZ_RE.test(iso) ? iso : `${iso}Z`;
  const ms = Date.parse(normalized);
  return Number.isNaN(ms) ? null : ms;
}

interface BeijingParts {
  year: number;
  month: number; // 1-12
  day: number;
  hour: number;
  minute: number;
}

/** 用固定 +8h 偏移量在 UTC getters 上读出"北京墙上时间"的日期时间分量,
 * 不使用 toLocaleString/Intl.DateTimeFormat(避免依赖运行时 ICU 数据)。 */
function toBeijingParts(iso: string): BeijingParts | null {
  const ms = toExactEpochMs(iso);
  if (ms === null) return null;
  const shifted = new Date(ms + BEIJING_OFFSET_MS);
  return {
    year: shifted.getUTCFullYear(),
    month: shifted.getUTCMonth() + 1,
    day: shifted.getUTCDate(),
    hour: shifted.getUTCHours(),
    minute: shifted.getUTCMinutes(),
  };
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/** "2026-08-07T17:00:00Z" → "2026-08-08 01:00"(注意日期会跨天)。
 * date_only 输入返回 null——调用方需要另外处理"没有精确时刻"的展示。 */
export function formatBeijingDateTime(iso: string): string | null {
  const p = toBeijingParts(iso);
  if (!p) return null;
  return `${p.year}-${pad2(p.month)}-${pad2(p.day)} ${pad2(p.hour)}:${pad2(p.minute)}`;
}

/** "2026-08-07T17:00:00Z" → "8月8日 01:00"(中文短格式,同样会跨天)。 */
export function formatBeijingZh(iso: string): string | null {
  const p = toBeijingParts(iso);
  if (!p) return null;
  return `${p.month}月${p.day}日 ${pad2(p.hour)}:${pad2(p.minute)}`;
}

/** "2026-08-07T17:00:00Z" → "01:00"——首页数据更新条只需要钟点,不需要
 * 日期(三条来源都是"今天最近一次成功",日期永远是今天)。 */
export function formatBeijingHM(iso: string): string | null {
  const p = toBeijingParts(iso);
  if (!p) return null;
  return `${pad2(p.hour)}:${pad2(p.minute)}`;
}

/** 精确 kickoff 对应的北京自然日"YYYY-MM-DD",供按天分组/排序使用。
 * date_only 输入返回 null(那种输入本身就没有"精确北京日"这个概念——
 * 来源只给了 UTC 自然日,不等于北京自然日,不能悄悄拿 UTC 日期顶替)。 */
export function beijingDateKey(iso: string): string | null {
  const p = toBeijingParts(iso);
  if (!p) return null;
  return `${p.year}-${pad2(p.month)}-${pad2(p.day)}`;
}
