/**
 * 比赛详情图表的球队配色解析——**全站唯一取色入口**(2026-08-24 建,2026-09-26 第四批统一)。
 *
 * 取色顺序(整对退级,不混搭):
 *   ① 本场 FotMob 配色(配对级,已按对手做过撞色规避)
 *   ② 该队近期代表色(该队最近一场有配色的比赛,后端 team_recent_brand_color)
 *   ③ 共享兜底组合 MATCH_FALLBACK_COLORS(青绿 / 琥珀,深浅两套主题各一份)
 * 每一级取到主客两色之后都做两项检查,任一方不通过则整对退到下一级:
 *   - 每个颜色对着调用方真实背景的对比度 ≥ 3:1(勉强不达标时在明度预算内朝
 *     "远离背景"的方向微调;浅色模式预算 15%,深色模式 25%,只改明度不改色相);
 *   - 主客两色 RGB 距离 ≥ 100(一眼能分开)。
 * 一方真实色、一方兜底色的混搭因此不会出现——整对要么都来自同一级,要么整对退级;
 * 检查对每一级的结果同样有效。兜底组合本身是对两种主题、两种背景(卡片底/球场底)
 * 都验证过的(见 tests/match-team-colors.test.ts)。
 *
 * 以下为历史背景(2026-08-24):
 *
 * 数据来源:FotMob general.teamColors,服务端已按对手做过撞色规避的
 * **主客配对级**结果(不是球队固定色,同一支队换个对手这两个值可能不同)。
 * 站长明确要求直接消费 FotMob 算好的结果,不自行重新实现其 CIE94 撞色规避
 * 算法——这里只做"选对主题变体 + 校验对比度安全 + 勉强不达标时小幅微调 +
 * 仍不安全或数据缺失时回退品牌色"这几件事(微调预算见 colorContrast.ts 的
 * nudgeForContrast/MAX_NUDGE_LIGHTNESS,2026-08-26 加入——此前"不达标就
 * 直接丢弃换品牌色"曾把只差 0.09:1 就达标的瓦伦西亚真实橙色整个换成了
 * 无关的品牌青绿,见 CLAUDE.md §11.4 事故记录)。
 *
 * 对比度必须对着调用方**真实渲染背景**算,不能猜——射门落点图的球场底色
 * 和其它图表的卡片底色不是同一个颜色,这是为什么每个图表分别调用
 * resolveMatchColors 而不是在 useChartColors() 内部统一处理(那个 hook
 * 不知道哪个图表会怎么用它)。
 *
 * 绝不跨主题借用另一份颜色:FotMob 深色模式下的客队配色可能是纯白
 * (示例真实值 darkMode.away="#ffffff"),这在深色背景上没问题,但如果被
 * 错误地当成浅色变体用在浅色球场(#F8FAFA)上就是白压白的隐形 bug——同一类
 * 错误 2026-08-24 已经在射门落点图的进球描边上踩过一次。缺失同主题变体时
 * 只回退品牌色,不去拿另一个主题的值顶替。
 */

import type { components } from "@/lib/api-types";
import {
  colorsDistinct,
  contrastRatioHex,
  isValidHexColor,
  MIN_CONTRAST,
  nudgeForContrast,
} from "./colorContrast";

export type TeamColorPair = components["schemas"]["TeamColorPair"];
/** 该队近期代表色(浅/深各一个十六进制值),后端 team_recent_brand_color。 */
export type TeamBrandColor = components["schemas"]["TeamBrandColor"];

/** 兜底组合(青绿 vs 琥珀):色相明确不同、不占用"红=真实错误"的语义;深浅各一份,
 * 对各自主题的卡片底与球场底对比度均 ≥3:1(单测守着)。 */
export const MATCH_FALLBACK_COLORS = {
  light: { home: "#087e78", away: "#b45309" },
  dark: { home: "#45b9af", away: "#f5a524" },
} as const;

/** 深色模式的明度微调预算(HSL lightness,0..1):FotMob 的 darkMode 值常常没有真的
 * 变亮(深蓝/深红在深色卡片底上只有 2.0–2.8:1),放宽到 25% 让它们提亮后保留;
 * 只朝远离背景的方向改明度,色相与饱和度不动。浅色模式仍是 6%。 */
export const DARK_NUDGE_LIGHTNESS = 0.25;
export const LIGHT_NUDGE_LIGHTNESS = 0.15;

export type ColorLevel = "match" | "team" | "fallback";

type PairLike = { light?: string | null; dark?: string | null } | null | undefined;

export type ColorOpts = {
  isDark: boolean;
  /** 调用方真实渲染背景(卡片底 --surface 或球场底 --pitch-neutral-bg):对比度必须对着它算 */
  backgroundHex: string;
  minContrast?: number;
};

export type ResolvedTeamColor = { hex: string; adjusted: boolean };

/**
 * 从一对深浅色里按当前主题选一个候选,校验合法且对着真实背景对比度达标;勉强不达标时
 * 在明度预算内微调。做不到返回 null(由调用方决定退级),不再返回任何"替代色"。
 * 不做任何跨主题借用:同主题变体缺失就是 null。
 */
export function resolveTeamColor(pair: PairLike, opts: ColorOpts): ResolvedTeamColor | null {
  const { isDark, backgroundHex, minContrast = MIN_CONTRAST } = opts;
  const candidate = isDark ? pair?.dark : pair?.light;
  if (!isValidHexColor(candidate)) return null;
  if (contrastRatioHex(candidate, backgroundHex) >= minContrast) {
    return { hex: candidate, adjusted: false };
  }
  // 2026-08-26 真实事故(瓦伦西亚 vs 皇家贝蒂斯):真实色对白底只差 0.09:1 就被整个丢掉。
  // 勉强不达标时先在预算内朝远离背景的方向微调明度救回真实色;预算内救不回来才退级。
  const budget = isDark ? DARK_NUDGE_LIGHTNESS : LIGHT_NUDGE_LIGHTNESS;
  const nudged = nudgeForContrast(candidate, backgroundHex, minContrast, budget);
  return nudged ? { hex: nudged, adjusted: true } : null;
}

export type MatchColorSources = {
  home?: { match?: PairLike; team?: PairLike };
  away?: { match?: PairLike; team?: PairLike };
};

export type ResolvedMatchColors = {
  home: string;
  away: string;
  /** 主客两色最终来自哪一级(整对同级) */
  level: ColorLevel;
  /** 该方颜色是否经过明度微调(与数据源原值不同) */
  homeAdjusted: boolean;
  awayAdjusted: boolean;
};

/** 某一级(本场 / 该队近期)的主客配对:两边都能取到、都通过对比度、且彼此可区分才算通过。 */
function tryLevel(
  home: PairLike,
  away: PairLike,
  opts: ColorOpts,
): Omit<ResolvedMatchColors, "level"> | null {
  const h = resolveTeamColor(home, opts);
  const a = resolveTeamColor(away, opts);
  if (!h || !a) return null;
  if (!colorsDistinct(h.hex, a.hex)) return null;
  return { home: h.hex, away: a.hex, homeAdjusted: h.adjusted, awayAdjusted: a.adjusted };
}

/** 唯一取色入口:本场配色 → 该队近期代表色 → 兜底组合,整对退级。 */
export function resolveMatchColors(
  sources: MatchColorSources,
  opts: ColorOpts,
): ResolvedMatchColors {
  const byMatch = tryLevel(sources.home?.match, sources.away?.match, opts);
  if (byMatch) return { ...byMatch, level: "match" };
  const byTeam = tryLevel(sources.home?.team, sources.away?.team, opts);
  if (byTeam) return { ...byTeam, level: "team" };
  const fb = opts.isDark ? MATCH_FALLBACK_COLORS.dark : MATCH_FALLBACK_COLORS.light;
  return { home: fb.home, away: fb.away, level: "fallback", homeAdjusted: false, awayAdjusted: false };
}
