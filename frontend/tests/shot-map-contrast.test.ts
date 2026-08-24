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
 * 里实际生效的十六进制颜色值抄进来(不是从组件里 import,因为颜色来自
 * CSS 自定义属性 + ECharts itemStyle 字面量,没有一个能直接拿到解析后
 * 十六进制值的入口——这正是 ECharts canvas 渲染和普通 DOM/CSS 的差异,见
 * useChartColors.ts 顶部注释),数值变化时人必须回来同步这份 fixture,
 * 这是有意的摩擦——顺手改配色不该在无声无息中溜过这道门槛。
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

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  return [
    parseInt(full.slice(0, 2), 16),
    parseInt(full.slice(2, 4), 16),
    parseInt(full.slice(4, 6), 16),
  ];
}

/** sRGB alpha 合成(标记半透明时用;当前 ShotMapChart 已不再用半透明,
 * alpha 恒为 1,但函数保留以便回归到半透明时这份测试仍然如实反映真相)。 */
function composite(fg: [number, number, number], bg: [number, number, number], alpha: number) {
  return fg.map((c, i) => Math.round(c * alpha + bg[i] * (1 - alpha))) as [number, number, number];
}

function relativeLuminance([r, g, b]: [number, number, number]): number {
  const s = [r, g, b].map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * s[0] + 0.7152 * s[1] + 0.0722 * s[2];
}

function contrastRatio(a: [number, number, number], b: [number, number, number]): number {
  const [l1, l2] = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x);
  return (l1 + 0.05) / (l2 + 0.05);
}

const MIN_CONTRAST = 3; // WCAG 非文字图形最低要求

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

  it.each(THEMES)("$label:标记描边(--ink)≥ 3:1(进球/非进球共用同一描边色)", ({ pitch, ink }) => {
    expect(contrastRatio(ink, pitch)).toBeGreaterThanOrEqual(MIN_CONTRAST);
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
});
