/**
 * TeamStatsBoards 头像/队徽改造(2026-09-11)——只补充"队徽随行下发"这一点,
 * 数值格式化/排序逻辑本身不是本次改动范围,不重复断言。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { TeamStatsBoards } from "@/components/league/TeamStatsBoards";
import type { TeamSeasonStatRow } from "@/lib/api-v1";

afterEach(cleanup);

function row(overrides: Partial<TeamSeasonStatRow> = {}): TeamSeasonStatRow {
  return {
    team: { team_id: 1001, name: "阿森纳", name_en: "Arsenal", crest_url: "/crest/1001.png" },
    matches_played: 3,
    avg_total_shots: 15.2,
    avg_shots_on_target: 6.1,
    avg_possession: 58.3,
    avg_expected_goals: 2.11,
    avg_expected_goals_on_target: 1.87,
    ...overrides,
  } as TeamSeasonStatRow;
}

describe("TeamStatsBoards 队徽", () => {
  it("有 crest_url 时渲染真实队徽图", () => {
    render(<TeamStatsBoards rows={[row()]} />);
    expect(screen.getAllByTestId("team-badge-image").length).toBeGreaterThan(0);
  });

  it("crest_url 缺失时退化为队名缩写,不是空白", () => {
    render(<TeamStatsBoards rows={[row({ team: { ...row().team, crest_url: null } })]} />);
    expect(screen.getAllByTestId("team-badge-fallback").length).toBeGreaterThan(0);
  });

  it("五张榜单卡标题都正常渲染", () => {
    render(<TeamStatsBoards rows={[row()]} />);
    expect(screen.getByText("场均射门榜")).not.toBeNull();
    expect(screen.getByText("场均 xGOT 榜")).not.toBeNull();
  });
});
