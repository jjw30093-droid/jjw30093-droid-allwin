import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { SlipCard, slipTone, type Slip } from "@/app/reco/page";

afterEach(cleanup);

function baseSlip(overrides: Partial<Slip> = {}): Slip {
  return {
    id: "slip-1",
    slip_date: "2026-08-16",
    title: "四分之一盘口测试单",
    note: null,
    combo_type: "single",
    status: "settled",
    result: "half_loss",
    return_units: 0.5,
    published_at: "2026-08-15T12:00:00Z",
    settled_at: "2026-08-16T20:00:00Z",
    edit_count: 0,
    last_edited_at: "2026-08-16T20:00:00Z",
    legs: [
      {
        id: "leg-1",
        match_id: 9001,
        match_desc: "主队 vs 客队 08-16 20:00",
        market: "ah",
        selection: "受让0.25",
        odds: 1.83,
        result: "half_win",
      },
    ],
    ...overrides,
  } as Slip;
}

describe("SlipCard 半赢/半输结果展示(2026-08-16 四分之一盘口扩展)", () => {
  it("slip.result='half_loss' 不应被误判为走水(push)色调——两者语义完全不同:走水是不赚不亏,half_loss 是净亏", () => {
    expect(slipTone(baseSlip())).toBe("half_loss");
  });

  it("half_loss 整单渲染出「半输」文案,不是「走水」也不是空白", () => {
    render(<SlipCard slip={baseSlip()} />);
    expect(screen.getByText("半输")).not.toBeNull();
    expect(screen.queryByText("走水")).toBeNull();
  });

  it("腿级 half_win 结果渲染出「半赢」,不是 undefined/空白(RESULT_ZH 三值硬编码会让新枚举值查不到文案)", () => {
    render(<SlipCard slip={baseSlip()} />);
    expect(screen.getByText("半赢")).not.toBeNull();
  });

  it("腿级 half_loss 同样渲染出「半输」文案", () => {
    const slip = baseSlip({
      legs: [
        {
          id: "leg-2",
          match_id: 9002,
          match_desc: "甲队 vs 乙队 08-16 22:00",
          market: "ah",
          selection: "受让0.25",
          odds: 1.9,
          result: "half_loss",
        },
      ],
    });
    render(<SlipCard slip={slip} />);
    // 左列色块 + 右列腿标签都会出现「半输」文案,断言至少存在一处
    expect(screen.getAllByText("半输").length).toBeGreaterThan(0);
  });
});
