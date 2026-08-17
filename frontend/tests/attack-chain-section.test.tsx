/**
 * AttackChainSection:某环节两队都无数据时不得画 0 宽度的条(那看起来像
 * "真实测出来是 0"),必须显式说明数据不足;某环节数据不完整(complete=false)
 * 时仍展示均值,但要有可见的部分数据标记。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { AttackChainSection } from "@/components/matches/AttackChainSection";
import type { components } from "@/lib/api-types";

afterEach(cleanup);

type AttackChain = components["schemas"]["MatchPreviewAttackChainDTO"];

function metric(value: number | null, complete = true, matches_with_data = 10) {
  return { value, complete, matches_with_data };
}

function fullChain(overrides: Partial<AttackChain> = {}): AttackChain {
  return {
    tier: "venue_full",
    matches: 10,
    label_zh: "近 10 个主场",
    volume_keys: ["opp_half_pass_share", "touches_opp_box", "shots", "shots_on_target", "xg", "xgot"],
    conversion_keys: ["shots_per_100_box_touches", "shot_on_target_rate", "xg_per_shot", "xgot_per_sot"],
    opp_half_pass_share: metric(52.3),
    touches_opp_box: metric(21.4),
    shots: metric(13.1),
    shots_on_target: metric(5.2),
    xg: metric(1.6),
    xgot: metric(1.3),
    shots_per_100_box_touches: metric(61.2),
    shot_on_target_rate: metric(39.7),
    xg_per_shot: metric(0.122),
    xgot_per_sot: metric(0.25),
    ...overrides,
  };
}

describe("AttackChainSection 缺失与部分数据的诚实展示", () => {
  it("正常两队都有数据时渲染六个环节的数值", () => {
    const home = fullChain();
    const away = fullChain({ label_zh: "近 10 个客场" });
    render(<AttackChainSection homeName="主队" awayName="客队" home={home} away={away} />);
    expect(screen.getByText(/进攻半场传球占比/)).not.toBeNull();
    expect(screen.getAllByText(/52\.3%/).length).toBeGreaterThan(0);
  });

  it("某环节两队都缺数据时显示数据不足文案,不画 0 宽度条", () => {
    const home = fullChain({ xgot: metric(null, false, 0) });
    const away = fullChain({ xgot: metric(null, false, 0) });
    const { container } = render(
      <AttackChainSection homeName="主队" awayName="客队" home={home} away={away} />,
    );
    expect(screen.getByText(/两队近期同主客场比赛都无该项数据/)).not.toBeNull();
    // 该行不应该出现 stageFillHome/stageFillAway 的 0 宽度条元素
    const fills = container.querySelectorAll('[class*="stageFillHome"], [class*="stageFillAway"]');
    // 进攻产量组其余 5 个环节 + 转化效率组 4 个环节,各两条(home+away)
    // = (5+4)*2 = 18 条,xgot 这一行不贡献条形。
    expect(fills.length).toBe(18);
  });

  it("某环节数据不完整时仍展示均值,并标记部分数据星号", () => {
    const home = fullChain({ xg: metric(1.6, false, 8) });
    const away = fullChain();
    const { container } = render(
      <AttackChainSection homeName="主队" awayName="客队" home={home} away={away} />,
    );
    const partial = container.querySelectorAll('[class*="partial"]');
    expect(partial.length).toBe(1);
    expect(screen.getByText(/有场次缺该字段/)).not.toBeNull();
  });

  it("只有一方缺数据时,另一方数值仍正常展示为条形", () => {
    const home = fullChain({ shots: metric(null, false, 0) });
    const away = fullChain();
    render(<AttackChainSection homeName="主队" awayName="客队" home={home} away={away} />);
    expect(screen.getAllByText(/数据不足/).length).toBeGreaterThanOrEqual(1);
  });

  it("验收返工三:一方 mixed、另一方 venue_full 时不得生成胜负式摘要", () => {
    // mixed(该队自己同场景样本不足、退回合并主客场)跟 venue_full(纯
    // 同场景窗口)口径不一样,不能拿来比"谁的均值更高"——即使两边数字
    // 本身都存在。原始数字仍然照常展示,被挡住的只是"差异最明显/更高"
    // 这句结论。
    const home = fullChain({ tier: "mixed" });
    const away = fullChain({ tier: "venue_full", label_zh: "近 10 个客场" });
    render(<AttackChainSection homeName="主队" awayName="客队" home={home} away={away} />);
    expect(screen.getByText(/样本口径不同,暂不作高低判断/)).not.toBeNull();
    expect(screen.queryByText(/差异最明显/)).toBeNull();
  });
});
