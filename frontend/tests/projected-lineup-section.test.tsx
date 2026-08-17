/**
 * ProjectedLineupSection 诚实文案测试(PIPELINE_REDESIGN_V2 P2)。
 *
 * 两个真实缺陷:
 * 1. 组件原来无条件承诺"更新后本区会换成「已确认首发」"——但真实抓取
 *    228 行 bronze_fm_lineup_snap 里 lineup_type 从未出现过 "confirmed",
 *    这是一个产品永远兑现不了的承诺(CLAUDE.md §2.2 禁止编造能力)。
 * 2. lineup_type="predicted"(source="enetpulse",16 行真实数据)被无条件
 *    渲染成"数据源给的是两队上一场的首发"——但 Enetpulse 的 predicted 是
 *    第三方对本场比赛的预测阵容,不是上一场的真实首发,这是一处独立的
 *    事实性错误,不是"确认/未确认"这一个维度能覆盖的。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ProjectedLineupSection } from "@/components/matches/ProjectedLineupSection";
import type { components } from "@/lib/api-types";

afterEach(cleanup);

type LineupSide = components["schemas"]["MatchPreviewLineupSideDTO"];

function side(over: Partial<LineupSide> = {}): LineupSide {
  return {
    team_id: 1,
    formation: "4-3-3",
    starters: [{ id: 1, name: "球员一", shirt_number: "1" }],
    subs: [],
    ...over,
  };
}

const BASE_PROPS = {
  homeName: "主队",
  awayName: "客队",
  observedAt: "2026-08-15T06:03:11Z",
  home: side(),
  away: side({ team_id: 2 }),
  homeSidelined: [],
  awaySidelined: [],
};

const UNKEEPABLE_PROMISE = "更新后本区会换成";

describe("ProjectedLineupSection 不承诺产品兑现不了的状态", () => {
  it.each([null, "lastStarting11", "predicted", "standard", "confirmed"])(
    "lineupType=%s 时都不出现「更新后本区会换成已确认首发」这句承诺",
    (lineupType) => {
      const { container } = render(
        <ProjectedLineupSection {...BASE_PROPS} lineupType={lineupType} />,
      );
      expect(container.textContent).not.toContain(UNKEEPABLE_PROMISE);
    },
  );
});

describe("ProjectedLineupSection predicted/enetpulse 诚实标注为第三方预测", () => {
  it("lineup_type=predicted 时不得声称是「两队上一场的首发」", () => {
    const { container } = render(
      <ProjectedLineupSection {...BASE_PROPS} lineupType="predicted" />,
    );
    expect(container.textContent).not.toContain("数据源给的是两队上一场的首发");
    expect(container.textContent).not.toContain("预计首发 · 基于上一场");
  });

  it("lineup_type=predicted 时文案标明这是预测/第三方来源", () => {
    const { container } = render(
      <ProjectedLineupSection {...BASE_PROPS} lineupType="predicted" />,
    );
    expect(container.textContent).toMatch(/预测/);
  });

  it("lineup_type=lastStarting11 时仍然标注为「基于上一场」(这个是真的)", () => {
    render(<ProjectedLineupSection {...BASE_PROPS} lineupType="lastStarting11" />);
    expect(screen.getAllByText("预计首发 · 基于上一场").length).toBeGreaterThan(0);
  });

  it("lineup_type 缺失/未知类型(standard 等)时不得冒充「上一场首发」", () => {
    const { container } = render(
      <ProjectedLineupSection {...BASE_PROPS} lineupType="standard" />,
    );
    expect(container.textContent).not.toContain("数据源给的是两队上一场的首发");
  });
});
