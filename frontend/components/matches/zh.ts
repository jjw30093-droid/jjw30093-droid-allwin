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

export const MARKET_ZH: Record<string, string> = {
  "1x2": "胜平负(欧赔)",
  ah: "亚洲让球",
  ou: "大小球",
  // 与 backend/queries/odds.py 的 _OPTION_MARKET_LABEL_ZH["corners_ou"] 措辞一致。
  corners_ou: "角球大小",
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
  // payload 形状与 "ou" 完全一样({line, over, under}),字段标签照抄。
  corners_ou: [
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

/** fact_match_events 的 Half 标记细分(2026-08-21 修复:此前时间线终场
 * 分隔行也硬编码显示"半场",与全场结束互相矛盾)。"全场"是本站既有词汇
 * (MatchHeaderFinished.tsx 的比分下方就写"全场")。 */
export const HALF_KIND_ZH: Record<string, string> = {
  HT: "中场",
  FT: "全场",
  AET: "加时赛结束",
};

// 2026-08-23 统一:AttemptSaved 目前无法区分"门将扑出"与"被后卫封堵"
// (数据源原始字段带 isBlocked,但采集端尚未落库),此前 zh.ts 写"被扑出"、
// ShotMapExplorer.tsx 写"被扑/被挡",同一页面两处措辞不一致。统一采用
// 更诚实的"被扑/被挡"——采集端补上 isBlocked 后再拆分成两个准确的值。
export const SHOT_OUTCOME_ZH: Record<string, string> = {
  Goal: "进球",
  AttemptSaved: "被扑/被挡",
  Miss: "偏出",
  Post: "中框",
};

export const SHOT_SITUATION_ZH: Record<string, string> = {
  RegularPlay: "运动战",
  // 与 backend/queries/matchup.py、backend/queries/team_style_preview.py
  // 的 FastBreak="反击" 对齐(此前这里单独写"快速反击",站内两个叫法)。
  FastBreak: "反击",
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

/** 天气枚举 key(FotMob content.weather.localizedKey)→ 中文。
 * 12 条全部取自 FotMob 官方安卓包的简体中文资源(2026-08-24 反编译对照表,
 * 资源名逐字一致),不是自译——命不中的新枚举值由调用方如实展示英文原文
 * (weather_description),不猜译文(CLAUDE.md §2.2)。 */
export const WEATHER_CONDITION_ZH: Record<string, string> = {
  weather_condition_clear: "晴朗",
  weather_condition_cloudy: "多云",
  weather_condition_foggy: "雾",
  weather_condition_heavy_rain: "大雨",
  weather_condition_light_rain: "小雨",
  weather_condition_partly_cloudy: "局部多云",
  weather_condition_rain: "雨",
  weather_condition_snowy: "雪",
  weather_condition_stormy: "风暴",
  weather_condition_sunny: "晴",
  weather_condition_thunderstorms: "雷暴",
  weather_condition_windy: "有风",
};

/** 场地表面(FotMob infoBox.Stadium.surface,来源原文小写英文)→ 中文。
 * 中文措辞取 FotMob 官方资源 surface_grass=天然草皮 / surface_artificial=
 * 人造草皮;来源 key 是网页版 payload 实测值("grass"/"artificial turf"),
 * 命不中的新值如实展示原文。 */
export const VENUE_SURFACE_ZH: Record<string, string> = {
  grass: "天然草皮",
  "artificial turf": "人造草皮",
};

/** 裁判统计项(infoBox.Referee.stats[].type)→ 中文。只列裁判卡展示的
 * perMatch 两项;matches/redCards 等 total 项当前不上卡。 */
export const REFEREE_STAT_ZH: Record<string, string> = {
  yellowCards: "黄牌",
  fouls: "犯规",
};

/** 裁判统计评级(服务端下发的 averageType,不在客户端自算阈值)→ 中文。
 * 配色注意:判罚尺度没有好坏之分,渲染侧统一中性色、只用文字表达方向,
 * 不得套用"绿=好/红=坏"(CLAUDE.md §11.2:红只用于真实错误)。 */
export const REFEREE_AVERAGE_TYPE_ZH: Record<string, string> = {
  below: "低于平均水平",
  average: "平均水平",
  above: "高于平均水平",
};

/** 球队数据对比行(顺序即展示顺序;format 决定数值渲染方式)。
 * key 与 MatchReportTeamStat 的字段名一一对应(Pydantic 单一真源生成)。 */
export const TEAM_STAT_LABELS: { key: string; label: string; format: "pct" | "num" | "num1" }[] = [
  { key: "possession", label: "控球率", format: "pct" },
  // "官方统计 xG"取自 FotMob 团队统计接口,与统计 tab 下方射门图自行按
  // shots[] 求和的"射门图 xG 合计"是两个独立来源,分别命名(不同名混称
  // 会让用户误以为数值不一致是 bug)。
  { key: "expected_goals", label: "官方统计 xG", format: "num1" },
  // 同一来源、同一命名逻辑的射正版本(数据倾向卡片 Fix 3,大小球市场折叠区补充)。
  { key: "expected_goals_on_target", label: "官方统计 xGOT", format: "num1" },
  // 2026-08-23 对照 FotMob 官方安卓包补充展示(此前已采集但从未渲染,
  // 数据库实测覆盖率 100%)。三项 xG 拆分与"官方统计 xG"同一来源、同一
  // 命名逻辑,只是拆到了进球来源这一维。
  { key: "expected_goals_open_play", label: "运动战 xG", format: "num1" },
  { key: "expected_goals_set_play", label: "定位球 xG", format: "num1" },
  { key: "expected_goals_non_penalty", label: "非点球 xG", format: "num1" },
  // 采纳 FotMob 官方措辞(与其安卓包资源名 total_shots 同 key 同中文)。
  { key: "total_shots", label: "射门次数", format: "num" },
  { key: "shots_on_target", label: "射正", format: "num" },
  // 射门图/球队统计口径的"未射正"(§11.2 修内部枚举泄漏:此前缺中文标签,
  // 英文 key 会直接渲染给用户)。
  { key: "shots_off_target", label: "射偏", format: "num" },
  { key: "big_chance", label: "绝佳机会", format: "num" },
  { key: "big_chance_missed", label: "错失绝佳机会", format: "num" },
  { key: "shots_inside_box", label: "禁区内射门", format: "num" },
  { key: "shots_outside_box", label: "禁区外射门", format: "num" },
  { key: "shots_woodwork", label: "击中门框", format: "num" },
  // 己方射门被对方封堵的次数(进攻视角)。采纳 FotMob 官方措辞(与其安卓包
  // 资源名 blocked_shots 同 key 同中文),同时与下面 shot_blocks(防守视角,
  // "封堵对方射门")拉开攻防方向差异,不再是"封堵射门"/"封堵对方射门"这种
  // 容易看错方向的一字之差。
  { key: "blocked_shots", label: "射门被封堵", format: "num" },
  // 采纳 FotMob 官方措辞(资源名 touches_opp_box,多一个"内"字消歧义)。
  { key: "touches_opp_box", label: "对方禁区内触球", format: "num" },
  { key: "passes", label: "传球总数", format: "num" },
  { key: "accurate_passes", label: "成功传球", format: "num" },
  { key: "own_half_passes", label: "己方半场传球", format: "num" },
  { key: "opposition_half_passes", label: "对方半场传球", format: "num" },
  { key: "long_balls_accurate", label: "成功长传", format: "num" },
  { key: "accurate_crosses", label: "成功传中", format: "num" },
  { key: "player_throws", label: "掷界外球", format: "num" },
  { key: "corners", label: "角球", format: "num" },
  { key: "tackles", label: "抢断", format: "num" },
  { key: "interceptions", label: "拦截", format: "num" },
  // 己方球员用身体封堵对方射门的次数(防守视角)——与上面 blocked_shots
  // (自己的射门被对方封堵,进攻视角)是两个方向相反的独立指标,不能共用
  // 一个中文名,否则两张表会显示出两个一模一样的"封堵射门"行。
  { key: "shot_blocks", label: "封堵对方射门", format: "num" },
  { key: "clearances", label: "解围", format: "num" },
  { key: "keeper_saves", label: "门将扑救", format: "num" },
  { key: "duel_won", label: "对抗成功", format: "num" },
  { key: "ground_duels_won", label: "地面对抗成功", format: "num" },
  { key: "aerials_won", label: "争顶成功", format: "num" },
  { key: "dribbles_succeeded", label: "成功过人", format: "num" },
  { key: "fouls", label: "犯规", format: "num" },
  { key: "offsides", label: "越位", format: "num" },
  { key: "yellow_cards", label: "黄牌", format: "num" },
  { key: "red_cards", label: "红牌", format: "num" },
];

/** 球队数据的分组(2026-08-23 对照 FotMob 官方安卓包核实:队级统计在 FotMob
 * 自己的比赛详情 payload 里就是 7 个有序分组——content.stats.Periods.All.stats
 * 是数组,每个元素带 title/key/stats[],我们的采集端此前把它拍平成
 * {key: value} 丢掉了分组信息。这里只是把分组信息在展示层补回来,不改
 * TEAM_STAT_LABELS 的扁平形状(MarketCard.tsx 依赖它是扁平的 key→label 表,
 * 见该文件 DRIVER_LABEL)。
 *
 * "重点数据"组是 FotMob 自己精选的摘要,字段会在其它组里重复出现——
 * statKeys 允许跨组重复,渲染层不必去重。某组内字段两侧全部缺失时,
 * 调用方负责整组不渲染(不产生空标题/空 <details>)。 */
export const TEAM_STAT_GROUPS: { key: string; label: string; statKeys: string[] }[] = [
  {
    key: "top_stats", label: "重点数据",
    statKeys: [
      "possession", "expected_goals", "total_shots", "shots_on_target",
      "touches_opp_box", "big_chance", "big_chance_missed", "accurate_passes",
      "yellow_cards", "corners",
    ],
  },
  {
    key: "shots", label: "射门",
    statKeys: [
      "total_shots", "shots_off_target", "shots_on_target", "blocked_shots",
      "shots_woodwork", "shots_inside_box", "shots_outside_box",
    ],
  },
  {
    key: "expected_goals", label: "预期进球",
    statKeys: [
      "expected_goals", "expected_goals_open_play", "expected_goals_set_play",
      "expected_goals_non_penalty", "expected_goals_on_target",
    ],
  },
  {
    key: "passes", label: "传球",
    statKeys: [
      "passes", "accurate_passes", "own_half_passes", "opposition_half_passes",
      "long_balls_accurate", "accurate_crosses", "player_throws",
      "touches_opp_box", "offsides",
    ],
  },
  {
    key: "defence", label: "防守",
    statKeys: ["tackles", "interceptions", "shot_blocks", "clearances", "keeper_saves"],
  },
  {
    key: "duels", label: "对抗",
    statKeys: ["duel_won", "ground_duels_won", "aerials_won", "dribbles_succeeded"],
  },
  {
    key: "discipline", label: "纪律",
    statKeys: ["yellow_cards", "red_cards", "fouls"],
  },
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

/** 比赛日展示日期。有精确 kickoff 时换算成**北京自然日**;来源只给了日期
 * (kickoff_at_utc 为 NULL,§6.2.1 的合法情况)时如实返回 UTC 自然日,并用
 * isBeijing=false 告诉调用方——绝不凭空 +8h 造一个北京日。措辞留给调用方
 * (页头要简洁、技术细节区可以啰嗦),这里只负责"换算 + 如实告知换没换成"。
 * 判据与 MatchListLive.tsx 的 matchDateKey() 完全一致,不另造第二套。 */
export function matchDayZh(
  kickoffAtUtc: string | null | undefined,
  dateUtc: string,
): { text: string; isBeijing: boolean } {
  if (kickoffAtUtc) {
    const key = beijingDateKey(kickoffAtUtc);
    if (key) return { text: formatDateZh(key), isBeijing: true };
  }
  return { text: formatDateZh(dateUtc), isBeijing: false };
}

const WEEKDAY_ZH = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

/** "2026-08-16" → "8月16日 周日"——/matches 列表按天分组的小标题。
 * 输入是纯自然日字符串(可能来自 beijingDateKey() 换算后的北京日,也可能是
 * 没有精确 kickoff 时退回的 date_utc 原始自然日),按日历字面值求星期即可,
 * 不需要再做时区换算,也不依赖 Intl/ICU。非法输入原样返回,不抛异常
 * (是否显示由调用方决定,这里只负责格式化)。 */
export function formatDateHeadingZh(dateKey: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateKey);
  if (!m) return dateKey;
  const [, y, mo, d] = m;
  const weekday = new Date(Date.UTC(Number(y), Number(mo) - 1, Number(d))).getUTCDay();
  return `${parseInt(mo, 10)}月${parseInt(d, 10)}日 ${WEEKDAY_ZH[weekday]}`;
}
