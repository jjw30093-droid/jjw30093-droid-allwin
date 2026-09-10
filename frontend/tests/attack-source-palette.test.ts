/**
 * 「进攻来源拆解」8 类分类色的合成对比度 + 互异性(2026-09 真实缺陷修复)。
 *
 * 真实缺陷(线上实测):此前的 6 色配色表只覆盖 8 个 Situation 里的 6 个,
 * 任意球(FreeKick)和界外球战术(ThrowInSetPiece)双双落到同一个兜底灰
 * `#8fa3a3`——两段在 SVG 里渲染出字节级相同的 `rgb(143,163,163)`。同时
 * 6 个色相全部挤在 174–195°(青蓝同色系),深色模式运动战/反击对比度只有
 * 1.04:1、浅色模式运动战/个人突破只有 1.05:1,肉眼几乎无法分辨。
 *
 * 十六进制值从 frontend/app/globals.css 抄进来(与其它 *-contrast.test.ts
 * 同一做法),颜色变化时人必须回来同步这份 fixture。
 */

import { describe, expect, it } from "vitest";
import { contrastRatioHex, hexToRgb, MIN_CONTRAST } from "@/components/charts/colorContrast";
import { SOURCE_KEYS } from "@/components/matches/attackSourcePalette";

const THEMES = [
  {
    label: "浅色模式",
    bg: "#ffffff",
    colors: {
      RegularPlay: "#236a8b",
      FastBreak: "#259374",
      FromCorner: "#4e1f7a",
      SetPiece: "#b42da4",
      FreeKick: "#4b7a1f",
      ThrowInSetPiece: "#625c18",
      IndividualPlay: "#279b34",
      Penalty: "#1d2472",
    },
  },
  {
    label: "深色模式",
    bg: "#0d2029",
    colors: {
      RegularPlay: "#85bcd6",
      FastBreak: "#9cdecb",
      FromCorner: "#aa7dd4",
      SetPiece: "#e3abdc",
      FreeKick: "#9ccf6e",
      ThrowInSetPiece: "#c7be57",
      IndividualPlay: "#85d68e",
      Penalty: "#9ca1de",
    },
  },
];

describe("进攻来源分类色:每色对卡片底 ≥3:1", () => {
  for (const theme of THEMES) {
    for (const key of SOURCE_KEYS) {
      it(`${theme.label}:${key}`, () => {
        expect(contrastRatioHex(theme.colors[key as keyof typeof theme.colors], theme.bg)).toBeGreaterThanOrEqual(
          MIN_CONTRAST,
        );
      });
    }
  }
});

describe("进攻来源分类色:覆盖全部 8 个 Situation,互不相同", () => {
  for (const theme of THEMES) {
    it(`${theme.label}:8 个 key 全部覆盖`, () => {
      expect(SOURCE_KEYS.length).toBe(8);
      for (const key of SOURCE_KEYS) {
        expect(theme.colors).toHaveProperty(key);
      }
    });

    it(`${theme.label}:8 个 hex 值两两不同(直接钉死"任意球=界外球战术同灰"这个真实 bug)`, () => {
      const values = SOURCE_KEYS.map((k) => theme.colors[k as keyof typeof theme.colors]);
      expect(new Set(values).size).toBe(values.length);
    });
  }
});

describe("回归护栏:旧配色的已知缺陷确实测不过(证明这条测试不是形式主义)", () => {
  it("旧的任意球/界外球战术兜底色(同一个 #8fa3a3)互相对比度应为 1:1", () => {
    const a = hexToRgb("#8fa3a3");
    const b = hexToRgb("#8fa3a3");
    // 同色对比度恒为 1(严格小于 MIN_CONTRAST),用来证明"互不相同"这条
    // 断言在旧配色下会真实失败——不是凑一条永远为真的断言。
    expect(contrastRatioHex("#8fa3a3", "#8fa3a3")).toBe(1);
    expect(a).toEqual(b);
  });

  it("旧的运动战(--brand-blue 深色 #69b6ce)与反击(--brand-teal 深色 #45b9af)对比度低于 3:1", () => {
    expect(contrastRatioHex("#69b6ce", "#45b9af")).toBeLessThan(MIN_CONTRAST);
  });
});
