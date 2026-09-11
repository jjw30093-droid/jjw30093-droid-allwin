/**
 * 榜首数值胶囊的球队配色解析(2026-09-11)。
 *
 * 后端下发的是该队在本赛季的一组代表色(浅/深各一个,见
 * backend/queries/teams.py::team_brand_color_map)。真实队色**不能直接拿来
 * 当底色**,两条对比度都得真算:
 *
 * 1. 胶囊里的数字 vs 胶囊底色 ≥ 4.5:1(WCAG 1.4.3 正文文字,硬门槛)——
 *    这是真正承载信息的一条:数字必须读得出来。所以前景色按底色明暗在
 *    白/深蓝之间二选一,不是固定白字。真实反例:曼城 #69A8D8 配白字只有
 *    2.2:1,必须改用深色字。
 * 2. 胶囊底色 vs 卡片底色 ≥ MIN_PILL_VS_CARD——这是"看得出这里有个色块"的
 *    下限,不是 WCAG 1.4.11 的 3:1。**刻意没用 3:1**,理由是 1.4.11 管的是
 *    "理解内容所必需的图形",而这个色块删掉之后榜单照样读得懂(第 1 名本来
 *    就在第一行、左边还有个"1"),它是强调、不是信息载体;数字的可读性由
 *    上面第 1 条单独保证,不依赖色块。实测如果这里卡 3:1,深色模式会几乎
 *    全军覆没:我们的深色卡片底是 #0d2029,而 FotMob 给的深色变体多是饱和
 *    蓝——切尔西 #033cc1 只有 1.93:1、埃弗顿 #0051d6 只有 2.51:1,两支都会
 *    被丢弃回退品牌色,整个功能等于没做。放宽到 1.5:1 保留的是它真正要挡的
 *    那类:队色和卡片底色近到根本看不出有胶囊。
 *
 * 勉强不达标时先用 nudgeForContrast 在小预算内微调救回真实色(§11.3 记过
 * 瓦伦西亚橙只差 0.09:1 就被整个丢弃换成品牌色的事故),预算内救不回来才
 * 整体回退品牌色。
 *
 * **两套主题必须都安全才上色**:只有一套达标就整体回退,否则同一支球队在
 * 浅色模式是队色、切到深色模式变品牌青绿,比统一用品牌色更难理解。
 *
 * 本文件是纯函数、无 "use client",LeaderboardCard 作为服务端组件直接调用;
 * 主题选择交给 CSS(见 LeaderboardCard.module.css 的 --pill-* 变量),不在
 * JS 里判断当前主题——服务端渲染时根本不知道用户选了哪套主题。
 */

import {
  contrastRatioHex,
  isValidHexColor,
  nudgeForContrast,
} from "@/components/charts/colorContrast";

/** 卡片底色 = globals.css 的 --surface,两套主题的实际取值。 */
const CARD_BG_LIGHT = "#ffffff";
const CARD_BG_DARK = "#0d2029";

/** 胶囊前景的两个候选:亮字与暗字(暗字取 --brand-navy,与 --ink 同源)。 */
const PILL_INK_ON_DARK = "#ffffff";
const PILL_INK_ON_LIGHT = "#0d2c3d";

/** 胶囊色块能从卡片底色里被看出来的下限(不是 WCAG 3:1,理由见文件头注释)。 */
const MIN_PILL_VS_CARD = 1.5;
/** 胶囊里的数字是正文文字,按 WCAG AA 要 4.5:1——这条是硬门槛,不放宽。 */
const MIN_INK_VS_PILL = 4.5;

export type TeamBrandColor = { light?: string | null; dark?: string | null };
export type PillPaint = { bg: string; ink: string };
export type PillPaintPair = { light: PillPaint; dark: PillPaint };

function paintFor(hex: string | null | undefined, cardBg: string): PillPaint | null {
  if (!isValidHexColor(hex)) return null;
  let bg: string = hex;
  if (contrastRatioHex(bg, cardBg) < MIN_PILL_VS_CARD) {
    // 勉强不达标先在小明度预算内救一把,不一刀切丢弃真实数据(§11.3)
    const nudged = nudgeForContrast(bg, cardBg, MIN_PILL_VS_CARD);
    if (!nudged) return null;
    bg = nudged;
  }
  // 前景按底色明暗二选一,不固定白字
  const ink =
    contrastRatioHex(PILL_INK_ON_DARK, bg) >= contrastRatioHex(PILL_INK_ON_LIGHT, bg)
      ? PILL_INK_ON_DARK
      : PILL_INK_ON_LIGHT;
  if (contrastRatioHex(ink, bg) < MIN_INK_VS_PILL) return null;
  return { bg, ink };
}

/** 两套主题都安全时返回配色,任一套不安全返回 null(调用方回退品牌色)。 */
export function resolvePillPaint(
  color: TeamBrandColor | null | undefined,
): PillPaintPair | null {
  if (!color) return null;
  const light = paintFor(color.light, CARD_BG_LIGHT);
  const dark = paintFor(color.dark, CARD_BG_DARK);
  if (!light || !dark) return null;
  return { light, dark };
}
