/**
 * 射门落点标记色 vs 球场底色的合成对比度(2026-08-24,CLAUDE.md §11.3)。
 *
 * 起因:2026-08-23 把非进球圆点从金/中性灰配色换成品牌青绿/蓝,压在绿茵球场
 * 上半透明(opacity:0.55)合成后对比度只有 1.09~1.17:1——`vitest run` 里没有
 * 任何测试会发现这个问题,因为 ShotMapChart.tsx 的 `option` 是内嵌在组件里
 * 由 hooks 派生的,没有一个能直接单元测试的 buildOption;而这类"渲染出来但
 * 肉眼看不见"的 bug,渲染冒烟测试(assert 不抛异常)本来就抓不到——只有真
 * 算合成对比度才能抓到。
 *
 * 本文件把 ShotMapChart.tsx / FootballPitchBackground.tsx / globals.css
 * 里实际生效的十六进制颜色值抄进来(颜色来自 CSS 自定义属性 + ECharts
 * itemStyle 字面量,不是从组件里 import 出运行期解析值——这正是 ECharts
 * canvas 渲染和普通 DOM/CSS 的差异,见 useChartColors.ts 顶部注释),数值
 * 变化时人必须回来同步这份 fixture,这是有意的摩擦——顺手改配色不该在
 * 无声无息中溜过这道门槛。
 *
 * 对比度数学本身(hexToRgb/relativeLuminance/contrastRatio)2026-08-24 起
 * 提到 components/charts/colorContrast.ts——球队真实配色(外部动态值)
 * 引入后,生产代码第一次需要在运行期真正跑这套数学做安全回退
 * (matchTeamColors.ts),不能再各写一份,两边必须是同一个实现。
 *
 * **只测「主题内一致」的组合**(浅色标记配浅色球场、深色标记配深色球场)
 * ——`--brand-teal`/`--pitch-neutral-bg` 等自定义属性随 `html[data-theme]`
 * 同步切换,浅色标记不可能真的出现在深色球场上,交叉配对没有product意义
 * (第一版这里测过全交叉,把"浅色标记 vs 深色球场"这种不会真实发生的组合
 * 也判成"失败",反而掩盖了下面这条真发现)。
 *
 * 本文件也是那条真发现的记录:进球描边最初照抄 FotMob 的硬编码白色,实测
 * 白色 vs 浅色中性球场(#F8FAFA)只有 1.05:1——白压白,近乎没描边。改用
 * `--ink`(浅色模式深、深色模式亮,永远与当前主题的球场底色反向)后两个
 * 主题都 ≥11:1。
 */

import { describe, expect, it } from "vitest";
import { contrastRatio, hexToRgb, MIN_CONTRAST, type Rgb } from "@/components/charts/colorContrast";

/** sRGB alpha 合成(标记半透明时用;当前 ShotMapChart 已不再用半透明,生产
 * 代码的 resolveTeamColor 也不做透明合成,只在这份测试里留着复现旧回归
 * 场景——不提进 colorContrast.ts,避免共享模块带一个生产用不到的函数)。 */
function composite(fg: Rgb, bg: Rgb, alpha: number): Rgb {
  return fg.map((c, i) => Math.round(c * alpha + bg[i] * (1 - alpha))) as Rgb;
}

// 与 frontend/app/globals.css 保持同步。每一档只配同主题内会真实出现的
// 球场底色 + 标记色 + 描边色组合(见模块顶部说明,不做无意义的交叉配对)。
const THEMES = [
  {
    label: "浅色模式",
    pitch: hexToRgb("#f8fafa"), // --pitch-neutral-bg(:root)
    ink: hexToRgb("#0d2c3d"), // --ink(:root,= --brand-navy)
    teal: hexToRgb("#087e78"), // --brand-teal(:root)
    navy: hexToRgb("#1d6f8b"), // --brand-blue(:root)
  },
  {
    label: "深色模式",
    pitch: hexToRgb("#333333"), // --pitch-neutral-bg(html[data-theme="dark"])
    ink: hexToRgb("#eef5f4"), // --ink(html[data-theme="dark"])
    teal: hexToRgb("#45b9af"), // --brand-teal(dark)
    navy: hexToRgb("#69b6ce"), // --brand-blue(dark)
  },
];

describe("射门落点标记 vs 中性球场底色 合成对比度(同主题组合)", () => {
  it.each(THEMES)("$label:主队标记(teal)不透明填色 ≥ 3:1", ({ pitch, teal }) => {
    expect(contrastRatio(composite(teal, pitch, 1), pitch)).toBeGreaterThanOrEqual(MIN_CONTRAST);
  });

  it.each(THEMES)("$label:客队标记(navy)不透明填色 ≥ 3:1", ({ pitch, navy }) => {
    expect(contrastRatio(composite(navy, pitch, 1), pitch)).toBeGreaterThanOrEqual(MIN_CONTRAST);
  });

  // 2026-08-25 起描边只用于实心圆(射正)与足球外圈(进球);空心圈的环色
  // 是球队色,不再有 --ink 描边——描述随之改写,数值断言不变。
  it.each(THEMES)("$label:实心圆/足球外圈描边(--ink)vs 球场底 ≥ 3:1", ({ pitch, ink }) => {
    expect(contrastRatio(ink, pitch)).toBeGreaterThanOrEqual(MIN_CONTRAST);
  });

  /* ── 2026-08-25 标记形状改版新增断言(方案 §2) ────────────────────── */

  // 数值与上面"不透明填色"两条相同,但语义不同(空心圈的**环线**色 vs
  // 填充色)——单独命名,日后有人把空心环改成灰色时这条才会挂掉。
  it.each(THEMES)("$label:空心圈环色(球队色)vs 球场底 ≥ 3:1", ({ pitch, teal, navy }) => {
    expect(contrastRatio(teal, pitch)).toBeGreaterThanOrEqual(MIN_CONTRAST);
    expect(contrastRatio(navy, pitch)).toBeGreaterThanOrEqual(MIN_CONTRAST);
  });

  // 前景压前景(此前完全没有的一类断言):足球图案(pitchBg 负空间色)画在
  // 球队色球体上,必须自己够对比,不能只测"标记 vs 背景"。
  it.each(THEMES)("$label:足球图案色(pitchBg)vs 球体色(球队色)≥ 3:1", ({ pitch, teal, navy }) => {
    expect(contrastRatio(pitch, teal)).toBeGreaterThanOrEqual(MIN_CONTRAST);
    expect(contrastRatio(pitch, navy)).toBeGreaterThanOrEqual(MIN_CONTRAST);
  });

  it("回归护栏 1:半透明(2026-08-23 的真实失败配置)必须被这份测试判定不达标", () => {
    // 当时的真实配置:brand-teal @ opacity 0.55 压在绿茵 #2c8a57 上。
    const oldGreenTurf = hexToRgb("#2c8a57");
    const oldTranslucentTeal = composite(hexToRgb("#087e78"), oldGreenTurf, 0.55);
    expect(contrastRatio(oldTranslucentTeal, oldGreenTurf)).toBeLessThan(MIN_CONTRAST);
  });

  it("回归护栏 2:白色描边在浅色中性球场上必须被这份测试判定不达标(本次实测踩过的坑)", () => {
    // 进球描边最初照抄 FotMob 的硬编码白色,没意识到新的浅色中性球场
    // (#F8FAFA)本身就是近白色——这条断言证明测试能抓住这类"复制别人配色但
    // 没对着自己的底色重新验证"的错误,不是只会抓旧 bug 的摆设。
    const white = hexToRgb("#ffffff");
    const lightPitch = hexToRgb("#f8fafa");
    expect(contrastRatio(white, lightPitch)).toBeLessThan(MIN_CONTRAST);
  });

  it.each(THEMES)(
    "回归护栏 3($label):墨色图案画在球队色球体上不达标——所以足球图案用 pitchBg 而不是 ink",
    ({ ink, teal, navy }) => {
      // 实测:浅色 ink vs teal 2.95 / ink vs navy 2.56,深色 2.16 / 2.07,
      // 四个组合全部 <3:1——把深色墨点画在球队色球体上是"看着顺眼但不达标"
      // 的典型(方案 §1.5 的数字)。与回归护栏 1/2 同一体例:证明测试能
      // 抓到这条本次真实踩过的坑,而不是只测正确答案。
      expect(contrastRatio(ink, teal)).toBeLessThan(MIN_CONTRAST);
      expect(contrastRatio(ink, navy)).toBeLessThan(MIN_CONTRAST);
    },
  );

  it("回归护栏 4:ECharts scatter 默认 opacity 0.8 会实质性降低合成对比度——标记必须显式 opacity: 1", () => {
    // 2026-08-25 实测发现的既有缺陷:scatter 的 itemStyle.opacity 默认 0.8,
    // ShotMapChart 从未显式设 1,线上真实渲染 teal 合成后只有 3.34(测试
    // fixture 按 alpha=1 断言的是 4.70)。resolveMatchColors 拿 @1.0 的
    // 球队色去比 3.0 阈值,一个恰好 3.05 的球队色经 0.8 合成后 ≈2.4——低于
    // 本项目硬规则却没有任何测试能抓到。custom 系列所有 style 显式
    // opacity: 1(探针确认 <circle> 输出无 opacity 属性),让 fixture 的
    // alpha=1 是事实而不是假设。
    const lightPitch = hexToRgb("#f8fafa");
    const teal = hexToRgb("#087e78");
    const composed = composite(teal, lightPitch, 0.8);
    expect(contrastRatio(composed, lightPitch)).toBeLessThan(4.7);
  });
});
