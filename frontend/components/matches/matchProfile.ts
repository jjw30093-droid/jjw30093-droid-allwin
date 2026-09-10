/**
 * 联赛百分位画像的纯计算/纯格式化函数——不带 `"use client"`(CLAUDE.md §11.4:
 * 服务端组件要 import 的纯函数必须放在不带 `"use client"` 的独立文件里,
 * 否则会在生产运行期抛"Attempted to call X from the server"异常,`next build`
 * 抓不到,只有真实请求才触发)。
 *
 * `MatchProfileOverview.tsx` / `PercentileGroupSection.tsx` 都从这里取
 * 数值格式化和措辞——不在组件 JSX 里做任何算术或文案判断。
 */

import type { components } from "@/lib/api-types";

export type MetricProfile = components["schemas"]["MatchProfileMetricDTO"];
export type GroupProfile = components["schemas"]["MatchProfileGroupDTO"];
export type Highlight = components["schemas"]["MatchProfileHighlightDTO"];
export type DataProfile = components["schemas"]["MatchDataProfileDTO"];
export type PeerTeam = components["schemas"]["MatchProfilePeerDTO"];

/** 后端 `comparison_mode` 的两种取值。
 *
 * - `league_percentile`:常规——两队各自对本联赛同场景分布取百分位。
 * - `cross_league_raw`:欧战等跨联赛赛事——两队来自不同国内联赛,没有共同的
 *   参照人群,只并排列出各自不限赛事的近期原始数值,全部百分位为 null。
 *
 * 本模块所有函数都把 `mode` 放在**可选末位参数**并默认 `league_percentile`
 * ——保证常规联赛路径的调用点和既有测试逐字不变。 */
export type ProfileMode = DataProfile["comparison_mode"];

export const DEFAULT_MODE: ProfileMode = "league_percentile";

/** 百分位 0~100 直接就是横轴上的位置(%),这里只做类型收窄 + 边界钳制。
 * `percentile` 为 null 时不应该被调用——调用方必须先判空,不画点。 */
export function dotLeftPct(percentile: number): number {
  return Math.max(0, Math.min(100, percentile));
}

export function formatMetricValue(value: number | null, unit: string, digits: number = 1): string {
  if (value == null) return "暂无数据";
  return `${value.toFixed(digits)}${unit}`;
}

/** 数值本身的小数位——xG/xGOT 类给 2 位,其余(次数/百分比)给 1 位。
 * 与后端 `league_percentile.py` 里各指标 `round()` 的精度保持一致的显示口径。 */
export function decimalsFor(unit: string): number {
  return unit.startsWith("球") ? 2 : 1;
}

/** 只有 `semantic === "style"` 的指标才是"打法描述",不能写"更强"——
 * 同 `backend/metrics/registry.py` 的措辞纪律(§一)。 */
export function isStyleMetric(semantic: string): boolean {
  return semantic === "style";
}

const GAP_WORDING: { min: number; label: string }[] = [
  { min: 40, label: "明显更强" },
  { min: 25, label: "更强一些" },
  { min: 15, label: "略占优势" },
  { min: 0, label: "基本持平" },
];

export function gapWording(gap: number): string {
  return GAP_WORDING.find((w) => gap >= w.min)!.label;
}

const STYLE_GAP_WORDING: { min: number; label: string }[] = [
  { min: 40, label: "明显更偏向" },
  { min: 25, label: "更偏向" },
  { min: 15, label: "略偏向" },
  { min: 0, label: "基本接近" },
];

export function styleGapWording(gap: number): string {
  return STYLE_GAP_WORDING.find((w) => gap >= w.min)!.label;
}

export function highlightSentence(h: Highlight, semantic: string, homeName: string, awayName: string): string {
  const leader = h.home_percentile >= h.away_percentile ? homeName : awayName;
  const leaderPct = Math.max(h.home_percentile, h.away_percentile);
  const otherPct = Math.min(h.home_percentile, h.away_percentile);
  const wording = isStyleMetric(semantic) ? styleGapWording(h.gap) : gapWording(h.gap);
  const verb = isStyleMetric(semantic) ? "" : "，";
  return `「${h.name_zh}」${leader}${verb}${wording}：联赛同场景第 ${leaderPct} 百分位对第 ${otherPct} 百分位。`;
}

/** 组级"谁更强"结论——tier 不兼容(某侧样本不足)时不下结论,只给口径说明。 */
/** 攻/守/控三组共用同一个窗口(venue_window,max_n=10/min_n=5),不像
 * `team_style_preview.py` 的象限图窗口——两处数字可能不同,不能暗示同口径。 */
export function profileWindowNote(homeName: string, awayName: string, profile: DataProfile): string {
  // 跨联赛模式的窗口跨了赛事,只写"近 N 个主场"会隐瞒这批比赛不在同一个
  // 联赛里——那是错误陈述,不是省略(同 window.py 里 label_zh 的同一条纪律)。
  const suffix = profile.comparison_mode === "cross_league_raw" ? "·不限赛事" : "";
  const home = profile.home_available ? `${homeName}(近 ${profile.home_matches} 个主场${suffix})` : `${homeName}(样本不足)`;
  const away = profile.away_available ? `${awayName}(近 ${profile.away_matches} 个客场${suffix})` : `${awayName}(样本不足)`;
  return `${home} · ${away}`;
}

/** 三段的标题:联赛模式叫「进攻百分位」,跨联赛模式没有百分位可言,
 * 叫「进攻数据」——标题里留着"百分位"三个字而下面一个分位都没有,
 * 比不给标题更让人困惑。 */
export function groupSectionTitle(titleZh: string, mode: ProfileMode = DEFAULT_MODE): string {
  return mode === "cross_league_raw" ? `${titleZh}数据` : `${titleZh}百分位`;
}

/** 差距门槛,与后端 `backend/metrics/percentile.py::GAP_FLOOR` 同源——
 * "差 1 分位和差 71 分位不能都算明显"就是那个常量的由来,这里不新造
 * 第二套判断标准。用于:①「最大的差距」榜(后端已用);②本模块默认
 * 展开哪几行。 */
export const GAP_FLOOR = 15;

/** 每组默认展开的行数上限/下限——超过上限的收进「展开全部」,
 * 不足下限时补齐,避免默认只剩一行孤零零。 */
export const DEFAULT_VISIBLE_MAX = 4;
export const DEFAULT_VISIBLE_MIN = 2;

export type MetricGap = {
  /** 两侧百分位之差的绝对值;任一侧缺百分位时为 null(不当作差距 0) */
  gap: number | null;
  /** 百分位更高的一侧——百分位在后端已按 direction 归一化("越低越好"的
   * 指标值小反而百分位高),所以这里直接比百分位就是"谁更好",前端不需要
   * 再按 direction 翻转一次(翻两次就翻回去了)。 */
  leader: "home" | "away" | null;
};

export function metricGap(metric: MetricProfile, mode: ProfileMode = DEFAULT_MODE): MetricGap {
  if (mode === "cross_league_raw") return rawGap(metric);
  const h = metric.home_percentile;
  const a = metric.away_percentile;
  if (h == null || a == null) return { gap: null, leader: null };
  if (h === a) return { gap: 0, leader: null };
  return { gap: Math.abs(h - a), leader: h > a ? "home" : "away" };
}

/** 跨联赛模式:`leader` 退化为**纯事实的"哪一侧的原始数字更大"**,
 * 刻意**不按 direction 归一化**。
 *
 * 联赛模式下百分位已按 direction 归一化,`leader` 就是"谁更好";跨联赛模式
 * 没有百分位,如果这里再按 direction 翻转(让 xGA 小的一方当 leader),胶囊
 * 就等于在说"这队防守更好"——而两队分处不同联赛、对手强度不同,这个判断
 * 站不住脚,也正是站长明确要求不下的那个结论。所以这里只陈述数字大小。
 *
 * `gap` 恒为 null:那个字段的语义是"百分位差",跨联赛模式没有百分位,
 * 把原始值差塞进同一个字段会让下游按百分位门槛(GAP_FLOOR)误判。 */
function rawGap(metric: MetricProfile): MetricGap {
  const hv = metric.home_value;
  const av = metric.away_value;
  if (hv == null || av == null) return { gap: null, leader: null };
  const digits = decimalsFor(metric.unit);
  const h = Number(hv.toFixed(digits));
  const a = Number(av.toFixed(digits));
  if (h === a) return { gap: null, leader: null };
  return { gap: null, leader: h > a ? "home" : "away" };
}

/** 跨联赛模式的排序依据:两侧原始值的**相对差**。
 *
 * 不能用绝对差——单位不同不可比(0.4球/场 和 3.2次/场 谁"差得多"没有意义)。
 * 分母取两侧均值;两边都是 0 时分母为 0,判定为"没有可读的差距"排到最后,
 * 不产出 Infinity/NaN。本组 12 个指标都是场均率值(xG≈1.5、射门≈13、
 * 控球率≈50),正常范围内分母远大于 0;将来若新增接近 0 的指标,这里要重新
 * 评估——相对差在近零分母上会被噪声放大。 */
function rawRelativeGap(metric: MetricProfile): number | null {
  const hv = metric.home_value;
  const av = metric.away_value;
  if (hv == null || av == null) return null;
  const scale = (Math.abs(hv) + Math.abs(av)) / 2;
  if (scale <= 0) return null;
  return Math.abs(hv - av) / scale;
}

/** 按 |Δ百分位| 降序;缺百分位的排最后(不是"差距 0",是"没法比")。
 * 并列按原顺序稳定排序(Array.prototype.sort 在现代引擎里是稳定的)。 */
export function sortMetricsByGap(
  metrics: MetricProfile[], mode: ProfileMode = DEFAULT_MODE,
): MetricProfile[] {
  const gapOf = mode === "cross_league_raw"
    ? rawRelativeGap
    : (m: MetricProfile) => metricGap(m).gap;
  return [...metrics].sort((x, y) => {
    const gx = gapOf(x);
    const gy = gapOf(y);
    if (gx == null && gy == null) return 0;
    if (gx == null) return 1;
    if (gy == null) return -1;
    return gy - gx;
  });
}

/** 默认展开哪几行:差距 ≥ GAP_FLOOR 的项,最多 DEFAULT_VISIBLE_MAX 项;
 * 达标项不足 DEFAULT_VISIBLE_MIN 时按差距顺序补齐。返回的是排好序的
 * 全量列表 + 分界下标,调用方据此切分"默认可见"与"折叠区"。 */
export function splitMetricsByGap(
  metrics: MetricProfile[], mode: ProfileMode = DEFAULT_MODE,
): {
  ordered: MetricProfile[];
  visibleCount: number;
} {
  const ordered = sortMetricsByGap(metrics, mode);
  if (mode === "cross_league_raw") {
    // GAP_FLOOR 是**百分位**差的门槛(与后端 percentile.py 同源),跨联赛
    // 模式没有百分位,把它挪用到相对差上就是拿一个无关口径当门槛。这里
    // 直接展开上限那几行,其余照常收进折叠区。
    return { ordered, visibleCount: Math.min(ordered.length, DEFAULT_VISIBLE_MAX) };
  }
  const qualified = ordered.filter((m) => {
    const g = metricGap(m).gap;
    return g != null && g >= GAP_FLOOR;
  }).length;
  const visibleCount = Math.min(
    ordered.length,
    Math.max(DEFAULT_VISIBLE_MIN, Math.min(DEFAULT_VISIBLE_MAX, qualified)),
  );
  return { ordered, visibleCount };
}

export type ValueDelta = {
  leader: "home" | "away";
  leaderName: string;
  /** 原始值方向:领先方的原始值比对方"更多"还是"更少"。防守类指标
   * (xGA、被射门)领先方的原始值反而更小,箭头必须如实向下——箭头表达
   * 的是原始值方向,"谁更好"由队名承担,两个通道各说各的,不冲突。 */
  dir: "up" | "down";
  /** 绝对差的展示串(不带符号,符号由箭头承担),含单位 */
  magnitude: string;
};

/** 「利物浦 ↑0.47球/场」——站长要的"多了多少"。任一侧缺百分位或缺原始值
 * 时返回 null(整个胶囊不渲染,不画 0——同 OddsTimeline 的 dir="unknown"
 * 不渲染的诚实纪律)。 */
export function valueDelta(
  metric: MetricProfile,
  homeName: string,
  awayName: string,
  mode: ProfileMode = DEFAULT_MODE,
): ValueDelta | null {
  // 跨联赛模式下 leader 是"原始值更大的一侧"(见 rawGap),所以下面算出来的
  // diff 恒为正、箭头恒向上——胶囊读作"这一队的这个数字多 N",纯陈述事实,
  // 不含"更强"的意思。措辞上的免责由组头文案承担。
  const { leader } = metricGap(metric, mode);
  if (leader == null) return null;
  const hv = metric.home_value;
  const av = metric.away_value;
  if (hv == null || av == null) return null;

  const digits = decimalsFor(metric.unit);
  // 差值必须从**页面上真实显示的那两个数**算起,不能用后端的高精度原始值:
  // 两侧各自四舍五入到 digits 位显示(1.69 / 1.22),若用原始值算差
  // (1.6851 − 1.2049 = 0.4802)胶囊会写 0.48,而用户自己一减是 0.47——
  // 一个"帮你省心算"的功能反而和页面上的数字对不上,比不给还糟。
  const round = (v: number) => Number(v.toFixed(digits));
  const leaderValue = round(leader === "home" ? hv : av);
  const otherValue = round(leader === "home" ? av : hv);
  const diff = leaderValue - otherValue;
  if (diff === 0) return null;

  return {
    leader,
    leaderName: leader === "home" ? homeName : awayName,
    dir: diff > 0 ? "up" : "down",
    magnitude: `${Math.abs(diff).toFixed(digits)}${metric.unit}`,
  };
}

/** "利物浦在联赛中类似什么球队水平"——站长原话要的对标句。百分位轴上
 * 20 支球队必然均匀摊开(百分位就是排名),看不出集团聚集,所以不做
 * "铺满全联赛队徽"这种视觉呈现,直接文字点名最接近的 1~2 支
 * (后端 `nearest_peers()` 已经按 |Δ百分位| 排好序,这里只格式化,不重排)。
 * 目标队自己没有组级百分位、或分布里够格的球队不足时,`peers` 为空数组,
 * 返回 null——调用方据此不渲染这一行,不编造"接近谁"的答案。 */
export function peerSentence(teamName: string, peers: PeerTeam[]): string | null {
  if (peers.length === 0) return null;
  const names = peers.map((p) => `${p.name}(${Math.round(p.percentile)})`).join("、");
  return `${teamName}接近${names}`;
}

export function groupVerdict(
  group: GroupProfile,
  semantic: string,
  homeName: string,
  awayName: string,
  mode: ProfileMode = DEFAULT_MODE,
): string {
  if (mode === "cross_league_raw") {
    // 返回空串 = 这一组不出结论行。绝不能走 gapWording()——那套措辞
    // ("明显更强"/"更强一些")的前提是两队在同一把百分位尺子上,跨联赛
    // 比赛没有这把尺子,下强弱结论就是编造。
    //
    // 但也不在这里写"不比强弱"那句免责:攻/守/控三张卡片各印一遍,同一句话
    // 在手机首屏重复三次(2026-09-10 站长复看时的原话是"说明的文字都太多了")。
    // 该免责由总览的 scope_note 承担,全页只说一次。
    return "";
  }
  const h = group.home_group_percentile;
  const a = group.away_group_percentile;
  if (h == null || a == null) {
    return "两队样本口径不同或数据不足，暂不作整体比较。";
  }
  const gap = Math.abs(h - a);
  if (gap < 15) return "两队在这一项上基本接近，没有拉开明显差距。";
  const leader = h >= a ? homeName : awayName;
  const wording = isStyleMetric(semantic) ? styleGapWording(gap) : gapWording(gap);
  return `${leader}${wording}。`;
}
