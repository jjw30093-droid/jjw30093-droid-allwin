/** 战绩写法统一(2026-10-02 站长 P0-3):单数 + 中/黑/走,盈利按每单 500 元换成钱,不再出现"单位"。 */
import { describe, expect, it } from "vitest";
import { fmtUnitsAsYuan } from "@/lib/profit-curve";
import { resultTallyText } from "@/lib/reco-labels";

const base = { win_count: 20, lose_count: 11, push_count: 2, half_win_count: 0, half_loss_count: 0 };

describe("resultTallyText", () => {
  it("中/黑/走,和首页'31 单 20 中'对得上", () => {
    expect(resultTallyText(base)).toBe("20中 11黑 2走");
  });
  it("四分之一盘半赢/半输只在出现时显示", () => {
    expect(resultTallyText({ ...base, half_win_count: 1, half_loss_count: 2 })).toBe("20中 1半赢 11黑 2半输 2走");
  });
});

describe("fmtUnitsAsYuan", () => {
  it("按每单 500 元换算,带千分位与正负号", () => {
    expect(fmtUnitsAsYuan(7.67)).toBe("+3,835 元");
    expect(fmtUnitsAsYuan(-1)).toBe("-500 元");
    expect(fmtUnitsAsYuan(0.9)).toBe("+450 元");
    expect(fmtUnitsAsYuan(0)).toBe("0 元");
  });
});
