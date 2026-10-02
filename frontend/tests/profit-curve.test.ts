import { describe, expect, it } from "vitest";
import { fmtYuan, profitScenario, toYuan } from "@/lib/profit-curve";

describe("盈利走势换算(每单 500 元、本金 10000 元)", () => {
  it("线上真实战绩 +7.67 单位 → +3,835 元,本金 10,000 → 13,835(+38.4%)", () => {
    const sc = profitScenario([{ slip_date: "2026-09-14", net_units: 7.67, cum_units: 7.67 }])!;
    expect(sc.totalYuan).toBe(3835);
    expect(sc.endBankrollYuan).toBe(13835);
    expect(sc.pct).toBe(38.4);
  });
  it("最大回撤 = 从之前最高点回落的最大金额", () => {
    const sc = profitScenario([
      { slip_date: "a", net_units: 1, cum_units: 1 },
      { slip_date: "b", net_units: -1, cum_units: 0 },
      { slip_date: "c", net_units: -1, cum_units: -1 },
      { slip_date: "d", net_units: 3, cum_units: 2 },
    ])!;
    expect(sc.maxDrawdownYuan).toBe(1000);
    expect(sc.from).toBe("a");
    expect(sc.to).toBe("d");
  });
  it("没有点就不出结论;亏损时显示负号", () => {
    expect(profitScenario([])).toBeNull();
    expect(fmtYuan(toYuan(-1.5))).toBe("-750 元");
    expect(fmtYuan(0)).toBe("0 元");
  });
});
