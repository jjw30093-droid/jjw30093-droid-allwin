/**
 * MatchStatsSection 的球队数据分组(2026-08-23 对照 FotMob 官方安卓包)。
 * 覆盖:分组渲染、空组不出现、单边缺失不画满条、两侧真零画空槽。
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MatchStatsSection } from "@/components/matches/MatchStatsSection";
import type { MatchReportResponse } from "@/lib/api-v1";

afterEach(cleanup);

type MatchReport = Extract<MatchReportResponse, { available: true }>;
type TeamStat = MatchReport["team_stats"][number];

function teamStat(overrides: Partial<TeamStat>): TeamStat {
  return {
    team_id: 1,
    is_home: true,
    period: "All",
    goals: null,
    possession: null,
    expected_goals: null,
    expected_goals_open_play: null,
    expected_goals_set_play: null,
    expected_goals_non_penalty: null,
    expected_goals_on_target: null,
    total_shots: null,
    shots_on_target: null,
    shots_off_target: null,
    big_chance: null,
    big_chance_missed: null,
    shots_inside_box: null,
    shots_outside_box: null,
    shots_woodwork: null,
    blocked_shots: null,
    touches_opp_box: null,
    passes: null,
    accurate_passes: null,
    own_half_passes: null,
    opposition_half_passes: null,
    long_balls_accurate: null,
    accurate_crosses: null,
    player_throws: null,
    corners: null,
    tackles: null,
    interceptions: null,
    shot_blocks: null,
    clearances: null,
    keeper_saves: null,
    duel_won: null,
    ground_duels_won: null,
    aerials_won: null,
    dribbles_succeeded: null,
    fouls: null,
    offsides: null,
    yellow_cards: null,
    red_cards: null,
    ...overrides,
  } as TeamStat;
}

const BASE_PROPS = {
  playerStats: [],
  homeName: "主队",
  awayName: "客队",
};

describe("MatchStatsSection 球队数据分组", () => {
  it("重点数据组直接展开(不在 <details> 里),其余有数据的组用 <details> 折叠", () => {
    const home = teamStat({ possession: 55, total_shots: 10, tackles: 12 });
    const away = teamStat({ is_home: false, team_id: 2, possession: 45, total_shots: 8, tackles: 9 });
    render(<MatchStatsSection {...BASE_PROPS} teamStats={[home, away]} />);
    // "重点数据"是普通标题,不是 <summary>
    const topHeading = screen.getByText("重点数据");
    expect(topHeading.closest("summary")).toBeNull();
    // "防守"组只有 tackles 有数据,应作为可折叠组出现
    const defenceSummary = screen.getByText("防守");
    expect(defenceSummary.closest("summary")).not.toBeNull();
  });

  it("完全没有数据的组不渲染(不出现空标题/空 <details>)", () => {
    // 只给 possession(落在"重点数据")和"预期进球"以外任何组都没有数据的字段
    const home = teamStat({ possession: 55 });
    const away = teamStat({ is_home: false, team_id: 2, possession: 45 });
    render(<MatchStatsSection {...BASE_PROPS} teamStats={[home, away]} />);
    expect(screen.queryByText("预期进球")).toBeNull();
    expect(screen.queryByText("传球")).toBeNull();
    expect(screen.queryByText("对抗")).toBeNull();
    expect(screen.queryByText("纪律")).toBeNull();
  });

  it("单边缺失时不画对比条(不能条说 0、数字说未知)", () => {
    const home = teamStat({ possession: 60, total_shots: 5 });
    const away = teamStat({ is_home: false, team_id: 2, possession: 40, total_shots: null });
    const { container } = render(<MatchStatsSection {...BASE_PROPS} teamStats={[home, away]} />);
    // total_shots 行:home=5 away=null,不应画出 barHome/barAway
    const rows = Array.from(container.querySelectorAll('[class*="compareRow"]'));
    const shotsRow = rows.find((r) => r.textContent?.includes("射门次数"));
    expect(shotsRow).toBeDefined();
    expect(shotsRow!.querySelector('[class*="barHome"]')).toBeNull();
    expect(shotsRow!.querySelector('[class*="barAway"]')).toBeNull();
  });

  it("两侧真为 0 时画空槽(轨道存在但两条色块都不画),不是 50/50", () => {
    const home = teamStat({ possession: 50, corners: 0 });
    const away = teamStat({ is_home: false, team_id: 2, possession: 50, corners: 0 });
    const { container } = render(<MatchStatsSection {...BASE_PROPS} teamStats={[home, away]} />);
    const rows = Array.from(container.querySelectorAll('[class*="compareRow"]'));
    const cornersRow = rows.find((r) => r.textContent?.includes("角球"));
    expect(cornersRow).toBeDefined();
    expect(cornersRow!.querySelector('[class*="barTrack"]')).not.toBeNull();
    expect(cornersRow!.querySelector('[class*="barHome"]')).toBeNull();
    expect(cornersRow!.querySelector('[class*="barAway"]')).toBeNull();
  });

  it("两队都没有球队统计数据时显示空态文案", () => {
    render(<MatchStatsSection {...BASE_PROPS} teamStats={[]} />);
    expect(screen.getByText("该场比赛暂无球队统计数据。")).not.toBeNull();
  });
});

describe("MatchStatsSection 全场/上半场/下半场切换", () => {
  it("没有半场数据时不显示切换器(诚实降级,不是显示空的半场)", () => {
    const home = teamStat({ possession: 55 });
    const away = teamStat({ is_home: false, team_id: 2, possession: 45 });
    render(<MatchStatsSection {...BASE_PROPS} teamStats={[home, away]} teamStatsByHalf={[]} />);
    expect(screen.queryByRole("tab", { name: "上半场" })).toBeNull();
  });

  it("有半场数据时可以切换,数字随切换变化", () => {
    const allHome = teamStat({ possession: 55 });
    const allAway = teamStat({ is_home: false, team_id: 2, possession: 45 });
    const firstHome = teamStat({ period: "FirstHalf", possession: 38 });
    const firstAway = teamStat({ is_home: false, team_id: 2, period: "FirstHalf", possession: 62 });
    render(
      <MatchStatsSection
        {...BASE_PROPS}
        teamStats={[allHome, allAway]}
        teamStatsByHalf={[firstHome, firstAway]}
      />,
    );
    expect(screen.getByText("55%")).not.toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "上半场" }));
    expect(screen.getByText("38%")).not.toBeNull();
    expect(screen.queryByText("55%")).toBeNull();
  });

  it("切到没有数据的半场时显示空态,不是崩溃或假数字", () => {
    const allHome = teamStat({ possession: 55 });
    const allAway = teamStat({ is_home: false, team_id: 2, possession: 45 });
    const firstHome = teamStat({ period: "FirstHalf", possession: 38 });
    // 只给主队种了上半场,客队没有 —— home/away 双方都要有数据才渲染对比条
    render(
      <MatchStatsSection
        {...BASE_PROPS}
        teamStats={[allHome, allAway]}
        teamStatsByHalf={[firstHome]}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "上半场" }));
    expect(screen.getByText("该场比赛暂无球队统计数据。")).not.toBeNull();
  });
});
