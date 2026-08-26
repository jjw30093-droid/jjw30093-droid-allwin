/**
 * 射门详情面板(2026-08-26 布局重做)的结构断言:标签-数值行取代旧的
 * 流水账文本行;球门框三条路径(onGoalShot 主路径 / 被封堵隐藏标记 /
 * goal_crossed 旧路径)在面板集成层各自走对分支。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ShotDetailPanel } from "@/components/matches/ShotDetailPanel";
import { BLOCKED_GOAL_MOUTH_TEXT } from "@/components/matches/GoalMouthDiagram";
import type { MatchReportResponse } from "@/lib/api-v1";

afterEach(cleanup);

type MatchReport = Extract<MatchReportResponse, { available: true }>;
type Shot = MatchReport["shots"][number];

function shot(overrides: Partial<Shot>): Shot {
  return {
    player_id: "p1",
    player_name: "萨卡",
    team_id: 1,
    is_home: true,
    minute: 27,
    period: "FirstHalf",
    x: 90,
    y: 34,
    xg: 0.31,
    xgot: 0.55,
    situation: "RegularPlay",
    outcome: "Goal",
    shot_type: "RightFoot",
    is_blocked: false,
    is_on_target: true,
    is_own_goal: false,
    is_own_goal_inferred: false,
    ...overrides,
  } as Shot;
}

const BASE_PROPS = {
  homeName: "阿森纳",
  awayName: "切尔西",
  onPrev: () => {},
  onNext: () => {},
  hasPrev: true,
  hasNext: true,
  position: 3,
  total: 12,
};

describe("ShotDetailPanel 标签-数值行", () => {
  it("结果/情境/部位/xG/xGOT 全部以标签行呈现,数值右列", () => {
    render(<ShotDetailPanel {...BASE_PROPS} shot={shot({})} />);
    expect(screen.getByText("结果")).toBeTruthy();
    expect(screen.getByText("进球")).toBeTruthy();
    expect(screen.getByText("情境")).toBeTruthy();
    expect(screen.getByText("运动战")).toBeTruthy();
    expect(screen.getByText("部位")).toBeTruthy();
    expect(screen.getByText("右脚")).toBeTruthy();
    expect(screen.getByText("预期进球 xG")).toBeTruthy();
    expect(screen.getByText("0.31")).toBeTruthy();
    expect(screen.getByText("射正预期进球 xGOT")).toBeTruthy();
    expect(screen.getByText("0.55")).toBeTruthy();
    expect(screen.getByText("萨卡")).toBeTruthy();
    expect(screen.getByText("3 / 12")).toBeTruthy();
  });

  it("缺失值显示 —,不静默填 0(§6.2)", () => {
    render(
      <ShotDetailPanel
        {...BASE_PROPS}
        shot={shot({ xg: null, xgot: null, situation: null, shot_type: null })}
      />,
    );
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(4);
    expect(screen.queryByText("0.00")).toBeNull();
  });
});

describe("ShotDetailPanel 球门框分支集成", () => {
  it("onGoalShot 数据存在 → 走 FotMob 主路径(画出 svg + 标记)", () => {
    const { container } = render(
      <ShotDetailPanel
        {...BASE_PROPS}
        shot={shot({
          outcome: "Miss",
          is_on_target: false,
          on_goal_shot_x: 0.0,
          on_goal_shot_y: 0.15,
          on_goal_shot_zoom_ratio: 0.32,
        })}
      />,
    );
    expect(container.querySelector("path[data-goal-frame]")).toBeTruthy();
    expect(container.querySelector("figure circle")).toBeTruthy();
    // 定性摘要,不编米数(米数只在 goal_crossed 口径下出现)
    expect(screen.getByText(/偏出左门柱外/)).toBeTruthy();
  });

  it("is_blocked=true → 隐藏标记,显示被封堵说明(即使带 onGoalShot 值)", () => {
    const { container } = render(
      <ShotDetailPanel
        {...BASE_PROPS}
        shot={shot({
          outcome: "AttemptSaved",
          is_blocked: true,
          on_goal_shot_x: 1.0,
          on_goal_shot_y: 0.3,
          on_goal_shot_zoom_ratio: 1.0,
        })}
      />,
    );
    expect(container.querySelector("figure circle")).toBeNull();
    expect(screen.getByText(BLOCKED_GOAL_MOUTH_TEXT)).toBeTruthy();
    expect(screen.getByText("被封堵")).toBeTruthy(); // 结果行的精确文案
  });

  it("历史场次(无 onGoalShot,有 goal_crossed)→ 旧路径不丢失", () => {
    const { container } = render(
      <ShotDetailPanel
        {...BASE_PROPS}
        shot={shot({
          on_goal_shot_x: null,
          on_goal_shot_y: null,
          on_goal_shot_zoom_ratio: null,
          goal_crossed_y: 34,
          goal_crossed_z: 1.2,
        })}
      />,
    );
    expect(container.querySelector("figure circle")).toBeTruthy();
    expect(screen.getByText(/距左门柱 3.66 米/)).toBeTruthy();
  });

  it("两类入网数据都没有 → 诚实空态文案", () => {
    render(
      <ShotDetailPanel
        {...BASE_PROPS}
        shot={shot({
          on_goal_shot_x: null,
          on_goal_shot_y: null,
          on_goal_shot_zoom_ratio: null,
          goal_crossed_y: null,
          goal_crossed_z: null,
        })}
      />,
    );
    expect(screen.getByText("该次射门没有可靠的入网位置数据。")).toBeTruthy();
  });
});
