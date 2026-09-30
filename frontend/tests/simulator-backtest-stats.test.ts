import { describe, expect, it } from "vitest";
import { binomPmf, binomTwoSidedP } from "@/features/simulator/backtest/stats";

describe("精确二项检验(v0.3 第 21 条)", () => {
  it("双侧 p 值与 scipy.stats.binomtest 一致", () => {
    expect(binomTwoSidedP(1, 14, 0.3087)).toBeCloseTo(0.07848328589435548, 10);
    expect(binomTwoSidedP(3, 10, 0.5)).toBeCloseTo(0.34375, 12);
    expect(binomTwoSidedP(9, 40, 0.35)).toBeCloseTo(0.134354668808916, 10);
  });
  it("概率质量之和为 1", () => {
    let s = 0;
    for (let k = 0; k <= 30; k++) s += binomPmf(k, 30, 0.27);
    expect(s).toBeCloseTo(1, 12);
  });
});
