/**
 * PossessionControlSection:控球率领先但禁区触球不领先时,文案不能笼统说
 * "控球更强",必须点出"控球和推进不完全同步"这类差异;两队都缺数据时
 * 不得画 0 宽度条。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { PossessionControlSection } from "@/components/matches/PossessionControlSection";
import type { components } from "@/lib/api-types";

afterEach(cleanup);

type PossessionControl = components["schemas"]["MatchPreviewPossessionControlDTO"];

function metric(value: number | null, complete = true, matches_with_data = 10) {
  return { value, complete, matches_with_data };
}

function fullControl(overrides: Partial<PossessionControl> = {}): PossessionControl {
  return {
    tier: "venue_full",
    matches: 10,
    label_zh: "近 10 个主场",
    possession: metric(55.0),
    pass_accuracy: metric(82.0),
    opp_half_pass_share: metric(45.0),
    touches_opp_box: metric(20.0),
    ...overrides,
  };
}

describe("PossessionControlSection", () => {
  it("控球和禁区触球领先方一致时,给出同步文案", () => {
    const home = fullControl();
    const away = fullControl({ possession: metric(40.0), touches_opp_box: metric(12.0), label_zh: "近 10 个客场" });
    render(<PossessionControlSection homeName="主队" awayName="客队" home={home} away={away} />);
    expect(screen.getByText(/控球更多地转化成了推进/)).not.toBeNull();
  });

  it("控球领先方与禁区触球领先方不一致时,指出不同步", () => {
    const home = fullControl({ touches_opp_box: metric(10.0) });
    const away = fullControl({ possession: metric(40.0), touches_opp_box: metric(22.0), label_zh: "近 10 个客场" });
    render(<PossessionControlSection homeName="主队" awayName="客队" home={home} away={away} />);
    expect(screen.getByText(/不完全同步/)).not.toBeNull();
  });

  it("某环节两队都缺数据时显示数据不足,不画 0 宽度条", () => {
    const home = fullControl({ pass_accuracy: metric(null, false, 0) });
    const away = fullControl({ pass_accuracy: metric(null, false, 0) });
    const { container } = render(
      <PossessionControlSection homeName="主队" awayName="客队" home={home} away={away} />,
    );
    expect(screen.getByText(/两队近期同主客场比赛都无该项数据/)).not.toBeNull();
    const fills = container.querySelectorAll('[class*="stageFillHome"], [class*="stageFillAway"]');
    expect(fills.length).toBe(6); // 其余 3 项各两条
  });

  it("验收返工四:中文页面不出现英文变量名 teal", () => {
    const home = fullControl();
    const away = fullControl({ label_zh: "近 10 个客场" });
    const { container } = render(
      <PossessionControlSection homeName="主队" awayName="客队" home={home} away={away} />,
    );
    expect(container.textContent ?? "").not.toContain("teal");
  });

  it("命名红线:文案里出现进攻半场传球占比时必须说明不是官方 Field Tilt", () => {
    const home = fullControl();
    const away = fullControl({ label_zh: "近 10 个客场" });
    render(<PossessionControlSection homeName="主队" awayName="客队" home={home} away={away} />);
    expect(screen.getByText(/不是 Opta\/StatsBomb 的官方 Field Tilt/)).not.toBeNull();
  });

  it("验收返工三:一方 mixed、另一方 venue_partial 时不得生成比较结论", () => {
    const home = fullControl({ tier: "mixed" });
    const away = fullControl({ tier: "venue_partial", label_zh: "近 7 个客场" });
    render(<PossessionControlSection homeName="主队" awayName="客队" home={home} away={away} />);
    expect(screen.getByText(/样本口径不同,暂不作高低判断/)).not.toBeNull();
    expect(screen.queryByText(/不完全同步/)).toBeNull();
    expect(screen.queryByText(/控球更多地转化成了推进/)).toBeNull();
  });
});
