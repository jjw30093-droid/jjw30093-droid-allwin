/**
 * 比赛详情图表的真实球队配色解析(2026-08-24)。
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
import { contrastRatioHex, isValidHexColor, MIN_CONTRAST, nudgeForContrast } from "./colorContrast";

export type TeamColorPair = components["schemas"]["TeamColorPair"];

/**
 * 从一对深浅色里按当前主题选一个候选,校验合法且对着真实背景对比度达标,
 * 否则回退到 fallbackHex。不做任何跨主题替代。
 */
export function resolveTeamColor(
  pair: TeamColorPair | null | undefined,
  opts: {
    isDark: boolean;
    backgroundHex: string;
    fallbackHex: string;
    minContrast?: number;
  },
): string {
  const { isDark, backgroundHex, fallbackHex, minContrast = MIN_CONTRAST } = opts;
  const candidate = isDark ? pair?.dark : pair?.light;
  if (!isValidHexColor(candidate)) return fallbackHex;
  if (contrastRatioHex(candidate, backgroundHex) >= minContrast) return candidate;
  // 2026-08-26 真实事故(瓦伦西亚 vs 皇家贝蒂斯):真实球队色 #ff671f 对白色
  // 卡片背景只有 2.91:1,比阈值差一点点就被直接丢弃换成品牌兜底色,导致
  // 势头图显示的颜色和 FotMob 官方完全不一样。勉强不达标时先在小预算内
  // 朝远离背景的方向微调明度救回真实色(见 nudgeForContrast 文档);预算内
  // 救不回来才真的回退品牌色——不是放宽阈值,是不让"差一点点"和"差很多"
  // 共用同一种"直接丢弃真实数据"的处理方式。
  const nudged = nudgeForContrast(candidate, backgroundHex, minContrast);
  return nudged ?? fallbackHex;
}

/** 主客队双方一起解析,调用方少写一次重复的 opts。 */
export function resolveMatchColors(
  home: TeamColorPair | null | undefined,
  away: TeamColorPair | null | undefined,
  opts: {
    isDark: boolean;
    backgroundHex: string;
    fallback: { home: string; away: string };
    minContrast?: number;
  },
): { home: string; away: string } {
  const { isDark, backgroundHex, fallback, minContrast } = opts;
  return {
    home: resolveTeamColor(home, {
      isDark,
      backgroundHex,
      fallbackHex: fallback.home,
      minContrast,
    }),
    away: resolveTeamColor(away, {
      isDark,
      backgroundHex,
      fallbackHex: fallback.away,
      minContrast,
    }),
  };
}
