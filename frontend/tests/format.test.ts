import { describe, expect, it } from "vitest";
import { formatOdds, formatPct, formatXg } from "@/lib/format";

describe("formatPct", () => {
  it("digits=0(默认)四舍五入到整数百分比", () => {
    expect(formatPct(0.618)).toBe("62%");
    expect(formatPct(0.5)).toBe("50%");
  });

  it("digits=1 保留一位小数", () => {
    expect(formatPct(0.618, 1)).toBe("61.8%");
  });
});

describe("formatOdds", () => {
  it("固定两位小数并补零", () => {
    expect(formatOdds(4)).toBe("4.00");
    expect(formatOdds(1.9)).toBe("1.90");
    expect(formatOdds(1.85)).toBe("1.85");
  });
});

describe("formatXg", () => {
  it("默认两位小数", () => {
    expect(formatXg(1.5)).toBe("1.50");
  });

  it("digits=1 保留一位小数", () => {
    expect(formatXg(1.5, 1)).toBe("1.5");
  });
});
