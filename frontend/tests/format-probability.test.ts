/**
 * 三项概率取整合计恒为 100(最大余数法,lib/format.ts::roundToHundred)。
 * 起因:线上首页重点卡 0.3866/0.3067/0.3067 逐项四舍五入显示 39/31/31,合计 101%。
 */

import { describe, expect, it } from "vitest";
import { roundToHundred, roundWdlPct } from "@/lib/format";

const sum = (a: number[]) => a.reduce((x, y) => x + y, 0);

describe("roundToHundred(最大余数法)", () => {
  it("线上真实例子:0.3866/0.3067/0.3067 → 38/31/31,合计 100(逐项取整是 39/31/31=101)", () => {
    expect(roundToHundred([0.3866, 0.3067, 0.3067])).toEqual([38, 31, 31]);
  });

  it("33.3 / 33.3 / 33.4 → 33/33/34", () => {
    expect(roundToHundred([0.333, 0.333, 0.334])).toEqual([33, 33, 34]);
  });

  it("39.4 / 30.9 / 30.7 → 39/31/30(差额补给小数部分最大的一项)", () => {
    expect(roundToHundred([0.394, 0.309, 0.307])).toEqual([39, 31, 30]);
  });

  it("三个完全相等的三分之一 → 34/33/33(小数部分相同按下标靠前补)", () => {
    expect(roundToHundred([1 / 3, 1 / 3, 1 / 3])).toEqual([34, 33, 33]);
  });

  it("恰好整数:50/30/20 原样返回", () => {
    expect(roundToHundred([0.5, 0.3, 0.2])).toEqual([50, 30, 20]);
  });

  it("逐项四舍五入会得到 99 的情形:33.5/33.5/33.0 无浮点误差地合计 100", () => {
    const r = roundToHundred([0.335, 0.335, 0.33])!;
    expect(sum(r)).toBe(100);
    expect(r).toEqual([34, 33, 33]);
  });

  it("合计略偏离 1(去水后四位小数取整:0.9999 / 1.0001)也先归一化,合计仍是 100", () => {
    expect(sum(roundToHundred([0.3333, 0.3333, 0.3333])!)).toBe(100);
    expect(sum(roundToHundred([0.3334, 0.3334, 0.3334])!)).toBe(100);
  });

  it("极端值:100/0/0 与 0/0/100", () => {
    expect(roundToHundred([1, 0, 0])).toEqual([100, 0, 0]);
    expect(roundToHundred([0, 0, 1])).toEqual([0, 0, 100]);
  });

  it("随机 5000 组:合计恒为 100,每项与精确值相差不到 1 个百分点,且不为负", () => {
    let seed = 42;
    const rnd = () => ((seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296);
    for (let n = 0; n < 5000; n++) {
      const a = rnd(), b = rnd(), c = rnd();
      const t = a + b + c;
      const probs = [a / t, b / t, c / t].map((v) => Math.round(v * 10000) / 10000);
      const r = roundToHundred(probs)!;
      expect(sum(r)).toBe(100);
      const tt = sum(probs);
      r.forEach((v, i) => {
        expect(v).toBeGreaterThanOrEqual(0);
        expect(Math.abs(v - (probs[i] / tt) * 100)).toBeLessThan(1);
      });
    }
  });

  it("无效输入返回 null:缺项/NaN/负数/合计为 0/空数组", () => {
    expect(roundToHundred([])).toBeNull();
    expect(roundToHundred([0.5, NaN, 0.5])).toBeNull();
    expect(roundToHundred([0.5, -0.1, 0.6])).toBeNull();
    expect(roundToHundred([0, 0, 0])).toBeNull();
    expect(roundToHundred([Infinity, 0, 0])).toBeNull();
  });
});

describe("roundWdlPct", () => {
  it("返回 [主胜, 平局, 客胜] 三元组;无效输入 null", () => {
    expect(roundWdlPct(0.3866, 0.3067, 0.3067)).toEqual([38, 31, 31]);
    expect(roundWdlPct(NaN, 0.5, 0.5)).toBeNull();
  });
});
