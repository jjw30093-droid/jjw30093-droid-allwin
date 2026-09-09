/**
 * 队徽象限图标记 vs 卡片底色的合成对比度(2026-09-09,CLAUDE.md §11.3)。
 *
 * 十六进制值从 frontend/app/globals.css 抄进来(与 shot-map-contrast.test.ts
 * 同一做法):颜色变化时人必须回来同步这份 fixture,这是有意的摩擦。
 *
 * 两条本次分析里的真发现,写成回归护栏:
 * 1. **品牌金不能做选中光环。** 刚换的金色品牌(commit faae4e0)是下一个人
 *    最容易顺手拿来用的颜色,但 --brand-gold #e9c037 压在白底只有 1.74:1。
 *    光环因此用 --ink(浅色模式深、深色模式亮,永远与底色反向)。
 * 2. **"调暗"不能靠降低象限色的透明度。** win/loss/teal 在 0.35–0.45 透明度
 *    下合成后只有 1.4–2.2:1,低于 3:1。所以调暗只作用于图片队徽(图片本身
 *    不承载单一色值语义),下限 0.55;无队徽的兜底圆点是真正的色彩载体,
 *    全不透明时必须 ≥3:1。
 */

import { describe, expect, it } from "vitest";
import { CREST } from "@/components/charts/crestQuadrantLayout";
import { contrastRatio, hexToRgb, MIN_CONTRAST, type Rgb } from "@/components/charts/colorContrast";

function composite(fg: Rgb, bg: Rgb, alpha: number): Rgb {
  return fg.map((c, i) => Math.round(c * alpha + bg[i] * (1 - alpha))) as Rgb;
}

// 与 frontend/app/globals.css 保持同步(只配同主题内会真实出现的组合)
const THEMES = [
  {
    label: "浅色模式",
    surface: hexToRgb("#ffffff"), // --surface = --paper-bright(:root)
    ink: hexToRgb("#0d2c3d"), // --ink(:root,= --brand-navy)
    ink2: hexToRgb("#40535d"), // --ink-2
    win: hexToRgb("#287851"),
    loss: hexToRgb("#b83b2d"),
    teal: hexToRgb("#087e78"),
    gold: hexToRgb("#e9c037"), // --brand-gold
  },
  {
    label: "深色模式",
    surface: hexToRgb("#0d2029"), // --paper-bright(html[data-theme="dark"])
    ink: hexToRgb("#eef5f4"),
    ink2: hexToRgb("#bdcbce"),
    win: hexToRgb("#68c994"),
    loss: hexToRgb("#ef7865"),
    teal: hexToRgb("#45b9af"),
    gold: hexToRgb("#f0ce55"),
  },
];

describe("队徽象限图标记 vs 卡片底色 合成对比度(同主题组合)", () => {
  it.each(THEMES)("$label:选中光环描边(--ink)≥ 3:1", ({ surface, ink }) => {
    expect(contrastRatio(ink, surface)).toBeGreaterThanOrEqual(MIN_CONTRAST);
  });

  it.each(THEMES)("$label:引线与真实坐标小圆点(--ink-2)≥ 3:1", ({ surface, ink2 }) => {
    expect(contrastRatio(ink2, surface)).toBeGreaterThanOrEqual(MIN_CONTRAST);
  });

  it.each(THEMES)(
    "$label:无队徽兜底圆点的三种象限色全不透明时 ≥ 3:1",
    ({ surface, win, loss, teal }) => {
      for (const color of [win, loss, teal]) {
        expect(contrastRatio(composite(color, surface, 1), surface)).toBeGreaterThanOrEqual(
          MIN_CONTRAST,
        );
      }
    },
  );

  it("调暗透明度下限 ≥ 0.5,防止以后被悄悄调低", () => {
    expect(CREST.DIM_OPACITY).toBeGreaterThanOrEqual(0.5);
    expect(CREST.BASE_OPACITY).toBeGreaterThan(CREST.DIM_OPACITY);
  });

  it("回归护栏 1:品牌金做光环在浅色底上必须被判定不达标", () => {
    const light = THEMES[0];
    expect(contrastRatio(light.gold, light.surface)).toBeLessThan(MIN_CONTRAST);
  });

  it("回归护栏 2:象限色降到 0.4 透明度就不达标——所以调暗不能作用在色彩载体上", () => {
    const light = THEMES[0];
    const dimmedTeal = composite(light.teal, light.surface, 0.4);
    expect(contrastRatio(dimmedTeal, light.surface)).toBeLessThan(MIN_CONTRAST);
  });
});
