/**
 * PercentileGroupSection / MatchProfileOverview 的百分位坐标点 vs 卡片/轨道
 * 底色合成对比度(2026-09,CLAUDE.md §11.3)。CSS 定位实现,不走 ECharts,
 * 没有 SSR 渲染冒烟测试可以自动兜住配色——这条测试是唯一的安全网。
 *
 * 2026-09 第二轮:站长反馈"轴上两个纯色点分不清哪个是哪队",点已经从
 * 纯色圆点换成 CrestDot(队徽 + 主/客队色描边,见 CrestDot.tsx)——队徽
 * 本身是任意配色的位图,合成对比度断言对它不成立,**描边**是现在唯一
 * 还能测的对象。描边色值(teal/blue)没有变,只是角色从"实心填充"变成
 * "2px 描边",这里的数值断言因此保持不变,只是措辞从"点"改成"描边"。
 *
 * 十六进制值从 frontend/app/globals.css 抄进来(与 team-quadrant-contrast.test.ts
 * 同一做法),颜色变化时人必须回来同步这份 fixture,这是有意的摩擦。
 */

import { describe, expect, it } from "vitest";
import { contrastRatio, hexToRgb, MIN_CONTRAST } from "@/components/charts/colorContrast";

const THEMES = [
  {
    label: "浅色模式",
    surface: hexToRgb("#ffffff"),
    surfaceSoft: hexToRgb("#eef2f2"),
    teal: hexToRgb("#087e78"),
    blue: hexToRgb("#1d6f8b"),
  },
  {
    label: "深色模式",
    surface: hexToRgb("#0d2029"),
    surfaceSoft: hexToRgb("#0b1d26"),
    teal: hexToRgb("#45b9af"),
    blue: hexToRgb("#69b6ce"),
  },
];

describe("队徽描边 vs 卡片底色(--surface)合成对比度", () => {
  it.each(THEMES)("$label:主队描边(teal)≥ 3:1", ({ surface, teal }) => {
    expect(contrastRatio(teal, surface)).toBeGreaterThanOrEqual(MIN_CONTRAST);
  });

  it.each(THEMES)("$label:客队描边(blue)≥ 3:1", ({ surface, blue }) => {
    expect(contrastRatio(blue, surface)).toBeGreaterThanOrEqual(MIN_CONTRAST);
  });
});

describe("队徽描边 vs 轨道底色(--surface-soft)合成对比度", () => {
  it.each(THEMES)("$label:主队描边(teal)≥ 3:1", ({ surfaceSoft, teal }) => {
    expect(contrastRatio(teal, surfaceSoft)).toBeGreaterThanOrEqual(MIN_CONTRAST);
  });

  it.each(THEMES)("$label:客队描边(blue)≥ 3:1", ({ surfaceSoft, blue }) => {
    expect(contrastRatio(blue, surfaceSoft)).toBeGreaterThanOrEqual(MIN_CONTRAST);
  });
});

describe("回归护栏:两队描边互相贴近时也不能只靠颜色区分(反例应当确实不达标)", () => {
  it("teal vs blue 之间的对比度本身低于 3:1(证明还需要队徽本身 + 位置冗余,不能只靠色相)", () => {
    const teal = hexToRgb("#087e78");
    const blue = hexToRgb("#1d6f8b");
    // 这条断言故意验证"两队描边色相接近、互相之间对比度不够"是真实存在的
    // 风险,不是假设——真正能认出"哪个是哪队"靠的是队徽本身(或降级的
    // 队名首字)+ 位置(left%),描边只是锦上添花的第二通道,不能指望
    // 色盲用户单靠这圈描边分清两队。
    expect(contrastRatio(teal, blue)).toBeLessThan(MIN_CONTRAST);
  });

  it("一个已知不达标的颜色(--brand-gold 压 --surface-soft)确实测不过,证明本测试不是形式主义", () => {
    const gold = hexToRgb("#e9c037");
    const surfaceSoft = hexToRgb("#eef2f2");
    expect(contrastRatio(gold, surfaceSoft)).toBeLessThan(MIN_CONTRAST);
  });
});
