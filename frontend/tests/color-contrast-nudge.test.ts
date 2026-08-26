/**
 * colorContrast.ts 的 HSL 转换与 nudgeForContrast() 单测(2026-08-26)。
 *
 * 起因见 CLAUDE.md §11.4:matchTeamColors.ts 此前对"勉强不达标"的真实球队色
 * 和"差得远"的球队色一视同仁,统一丢弃换成品牌兜底色——瓦伦西亚真实橙色
 * #ff671f 对白色背景只差 0.09 就达标(2.91:1 vs 阈值 3:1),结果被整个换掉。
 * nudgeForContrast() 在小预算(MAX_NUDGE_LIGHTNESS)内朝远离背景的方向微调
 * 明度救回勉强不达标的真实色;预算耗尽仍不达标则返回 null,调用方据此回退
 * 品牌色——这条边界本身必须被测到,不能只测"能救回"这一侧。
 */

import { describe, expect, it } from "vitest";
import {
  contrastRatioHex,
  hexToRgb,
  hslToRgb,
  MAX_NUDGE_LIGHTNESS,
  nudgeForContrast,
  rgbToHex,
  rgbToHsl,
} from "@/components/charts/colorContrast";

describe("rgbToHsl / hslToRgb 往返", () => {
  it.each(["#ff671f", "#035db8", "#ffffff", "#000000", "#808080", "#009048"])(
    "%s 转 HSL 再转回 RGB,误差在取整范围内",
    (hex) => {
      const rgb = hexToRgb(hex);
      const hsl = rgbToHsl(rgb);
      const back = hslToRgb(hsl);
      for (let i = 0; i < 3; i++) {
        expect(Math.abs(back[i] - rgb[i])).toBeLessThanOrEqual(1);
      }
    },
  );

  it("灰阶(饱和度为 0)不会因为 hue 计算除零而产生 NaN", () => {
    const hsl = rgbToHsl(hexToRgb("#808080"));
    expect(hsl.every((v) => Number.isFinite(v))).toBe(true);
    const back = rgbToHex(hslToRgb(hsl));
    expect(back).toBe("#808080");
  });
});

describe("nudgeForContrast", () => {
  it("已经达标时原样返回,不做任何调整", () => {
    const v = nudgeForContrast("#104070", "#f8fafa", 3);
    expect(v).toBe("#104070");
  });

  it("2026-08-26 真实事故案例:瓦伦西亚 #ff671f 对白色背景 2.91:1,预算内可救回", () => {
    expect(contrastRatioHex("#ff671f", "#ffffff")).toBeLessThan(3);
    const v = nudgeForContrast("#ff671f", "#ffffff", 3);
    expect(v).not.toBeNull();
    expect(contrastRatioHex(v as string, "#ffffff")).toBeGreaterThanOrEqual(3);
    // 微调幅度必须落在预算内,不能悄悄超支
    const [, , l0] = rgbToHsl(hexToRgb("#ff671f"));
    const [, , l1] = rgbToHsl(hexToRgb(v as string));
    expect(Math.abs(l0 - l1)).toBeLessThanOrEqual(MAX_NUDGE_LIGHTNESS + 0.005);
  });

  it("差得远的案例(#035db8 对深色球场 #333333 只有 1.96:1,需要约 11% 明度)预算内救不回,返回 null", () => {
    expect(contrastRatioHex("#035db8", "#333333")).toBeLessThan(3);
    const v = nudgeForContrast("#035db8", "#333333", 3);
    expect(v).toBeNull();
  });

  it("调整方向必须正确:前景比背景暗时继续变暗,不能调反方向让对比度变得更差", () => {
    const before = contrastRatioHex("#ff671f", "#ffffff");
    const v = nudgeForContrast("#ff671f", "#ffffff", 3);
    expect(v).not.toBeNull();
    expect(contrastRatioHex(v as string, "#ffffff")).toBeGreaterThan(before);
  });

  it("前景比背景亮时朝变亮方向调整(深色背景场景)", () => {
    // 一个比深色背景 #333333 略亮、但不够达标的颜色
    const dim = "#4a4a4a";
    expect(contrastRatioHex(dim, "#333333")).toBeLessThan(3);
    const v = nudgeForContrast(dim, "#333333", 3);
    if (v !== null) {
      const [, , l0] = rgbToHsl(hexToRgb(dim));
      const [, , l1] = rgbToHsl(hexToRgb(v));
      expect(l1).toBeGreaterThanOrEqual(l0);
    }
  });
});
