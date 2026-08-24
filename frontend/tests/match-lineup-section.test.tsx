/**
 * MatchLineupSection(阵容 tab:阵型图 + 首发/替补名单)基础渲染 +
 * 2026-08-24 球员头像接入。此前零测试覆盖,这次顺手补上最基本的正确性断言。
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MatchLineupSection } from "@/components/matches/MatchLineupSection";
import type { MatchReportResponse } from "@/lib/api-v1";

afterEach(cleanup);

type MatchReport = Extract<MatchReportResponse, { available: true }>;
type LineupTeam = MatchReport["lineups"][number];
type LineupPlayer = LineupTeam["starters"][number];

function player(overrides: Partial<LineupPlayer>): LineupPlayer {
  return {
    player_id: "1",
    name: "球员",
    is_starter: true,
    is_captain: false,
    ...overrides,
  };
}

function team(isHome: boolean, starters: LineupPlayer[], bench: LineupPlayer[] = []): LineupTeam {
  // player() 不带 pitch_x/pitch_y,PitchFormation 因此不会画点(见
  // pitch-formation.test.tsx 的"双方都没有站位坐标时整个组件不渲染"),
  // 本文件的头像数量断言可以只针对 TeamColumn/PlayerRow,不受阵型图干扰。
  return { team_id: isHome ? 1 : 2, is_home: isHome, formation: "4-3-3", starters, bench };
}

describe("MatchLineupSection", () => {
  it("双方都有阵容时,首发和替补列表行都渲染头像", () => {
    const home = team(
      true,
      [player({ player_id: "h1", name: "主队门将", shirt_number: "1" })],
      [player({ player_id: "h2", name: "主队替补", shirt_number: "12" })],
    );
    const away = team(false, [player({ player_id: "a1", name: "客队门将", shirt_number: "1" })]);
    const { container } = render(
      <MatchLineupSection lineups={[home, away]} homeName="主队" awayName="客队" />,
    );
    const images = container.querySelectorAll('[data-testid="player-avatar-image"] img');
    expect(images.length).toBe(3);
    expect(screen.getByText("主队门将")).not.toBeNull();
    expect(screen.getByText("主队替补")).not.toBeNull();
    expect(screen.getByText("客队门将")).not.toBeNull();
  });

  it("头像加载失败时列表行回退成球衣号文字,不留裂图标", () => {
    const home = team(true, [player({ player_id: "h1", name: "主队门将", shirt_number: "1" })]);
    const away = team(false, [player({ player_id: "a1", name: "客队门将", shirt_number: "2" })]);
    const { container } = render(
      <MatchLineupSection lineups={[home, away]} homeName="主队" awayName="客队" />,
    );
    const img = container.querySelector('[data-testid="player-avatar-image"] img')!;
    fireEvent.error(img);
    expect(container.querySelector('[data-testid="player-avatar-fallback"]')).not.toBeNull();
    expect(container.querySelectorAll('[data-testid="player-avatar-image"] img').length).toBe(1);
  });

  it("两队均无阵容数据时显示空态文案,不渲染头像", () => {
    const { container } = render(<MatchLineupSection lineups={[]} homeName="主队" awayName="客队" />);
    expect(screen.getByText("该场比赛暂无阵容数据。")).not.toBeNull();
    expect(container.querySelector('[data-testid="player-avatar-image"]')).toBeNull();
  });
});
