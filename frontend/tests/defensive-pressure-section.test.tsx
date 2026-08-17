/**
 * DefensivePressureSection:xGA 更低的一方在文案里应被描述为限制能力更强;
 * 两队都缺数据时不得画 0 宽度条;文案不得出现拦截/解围/封堵等防守动作词汇
 * (那些是防守动作/风格,不是这张图该展示的防守结果)。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { DefensivePressureSection } from "@/components/matches/DefensivePressureSection";
import type { components } from "@/lib/api-types";

afterEach(cleanup);

type DefensivePressure = components["schemas"]["MatchPreviewDefensivePressureDTO"];

function metric(value: number | null, complete = true, matches_with_data = 10) {
  return { value, complete, matches_with_data };
}

function fullPressure(overrides: Partial<DefensivePressure> = {}): DefensivePressure {
  return {
    tier: "venue_full",
    matches: 10,
    label_zh: "近 10 个主场",
    shots_faced: metric(11.0),
    shots_on_target_faced: metric(4.0),
    xga: metric(1.2),
    box_shots_faced: metric(6.0),
    ...overrides,
  };
}

describe("DefensivePressureSection", () => {
  it("xGA 更低的一方在文案里被描述为限制能力更强", () => {
    const home = fullPressure({ xga: metric(0.9) });
    const away = fullPressure({ xga: metric(1.6), label_zh: "近 10 个客场" });
    render(<DefensivePressureSection homeName="主队" awayName="客队" home={home} away={away} />);
    expect(screen.getByText(/主队近期让对手打出的期望进球更少/)).not.toBeNull();
  });

  it("某环节两队都缺数据时显示数据不足,不画 0 宽度条", () => {
    const home = fullPressure({ box_shots_faced: metric(null, false, 0) });
    const away = fullPressure({ box_shots_faced: metric(null, false, 0) });
    const { container } = render(
      <DefensivePressureSection homeName="主队" awayName="客队" home={home} away={away} />,
    );
    expect(screen.getByText(/两队近期同主客场比赛都无该项数据/)).not.toBeNull();
    const fills = container.querySelectorAll('[class*="stageFillHome"], [class*="stageFillAway"]');
    expect(fills.length).toBe(6);
  });

  it("验收返工四:中文页面不出现英文变量名 teal", () => {
    const home = fullPressure();
    const away = fullPressure({ label_zh: "近 10 个客场" });
    const { container } = render(
      <DefensivePressureSection homeName="主队" awayName="客队" home={home} away={away} />,
    );
    expect(container.textContent ?? "").not.toContain("teal");
  });

  it("验收返工四:xGA 首次出现要有中文全称解释", () => {
    const home = fullPressure();
    const away = fullPressure({ label_zh: "近 10 个客场" });
    render(<DefensivePressureSection homeName="主队" awayName="客队" home={home} away={away} />);
    expect(screen.getAllByText(/预期失球/).length).toBeGreaterThan(0);
  });

  it("不展示拦截/解围/封堵这类防守动作词汇", () => {
    const home = fullPressure();
    const away = fullPressure({ label_zh: "近 10 个客场" });
    const { container } = render(
      <DefensivePressureSection homeName="主队" awayName="客队" home={home} away={away} />,
    );
    const text = container.textContent ?? "";
    // 脚注里允许提到这些词是为了说明"不包含"它们,但不能作为可比较的数值行出现
    expect(container.querySelectorAll('[class*="stageLabel"]').length).toBe(4);
    expect(text).toContain("不包含拦截、解围、封堵");
  });

  it("验收返工三:tier 不兼容时不得生成谁限制能力更强的结论", () => {
    const home = fullPressure({ tier: "mixed", xga: metric(0.9) });
    const away = fullPressure({ tier: "venue_full", xga: metric(1.6), label_zh: "近 10 个客场" });
    render(<DefensivePressureSection homeName="主队" awayName="客队" home={home} away={away} />);
    expect(screen.getByText(/样本口径不同,暂不作高低判断/)).not.toBeNull();
    expect(screen.queryByText(/让对手打出的期望进球更少/)).toBeNull();
  });
});
