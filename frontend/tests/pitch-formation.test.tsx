/**
 * PitchFormation(真实首发阵型图)基础渲染 + 2026-08-24 球员头像接入。
 * 此前零测试覆盖,这次顺手补上最基本的正确性断言。
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { PitchFormation } from "@/components/matches/PitchFormation";
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

function team(isHome: boolean, starters: LineupPlayer[]): LineupTeam {
  return { team_id: isHome ? 1 : 2, is_home: isHome, formation: "4-3-3", starters, bench: [] };
}

describe("PitchFormation", () => {
  it("双方都有站位坐标时,画出球场且每名首发都有头像", () => {
    const home = team(true, [
      player({ player_id: "h1", name: "主队门将", shirt_number: "1", pitch_x: 0.1, pitch_y: 0.5 }),
      player({ player_id: "h2", name: "主队前锋", shirt_number: "9", pitch_x: 0.8, pitch_y: 0.5 }),
    ]);
    const away = team(false, [
      player({ player_id: "a1", name: "客队门将", shirt_number: "1", pitch_x: 0.1, pitch_y: 0.5 }),
    ]);
    const { container } = render(<PitchFormation home={home} away={away} />);
    const images = container.querySelectorAll('[data-testid="player-avatar-image"] img');
    expect(images.length).toBe(3);
    expect(screen.getByText("1 主队门将")).not.toBeNull();
    expect(screen.getByText("9 主队前锋")).not.toBeNull();
  });

  it("任一队全员缺站位坐标时该队不画点,不猜位置", () => {
    const home = team(true, [
      player({ player_id: "h1", name: "主队门将", pitch_x: null, pitch_y: null }),
    ]);
    const away = team(false, [
      player({ player_id: "a1", name: "客队门将", pitch_x: 0.1, pitch_y: 0.5 }),
    ]);
    const { container } = render(<PitchFormation home={home} away={away} />);
    expect(screen.getByText(/客队门将/)).not.toBeNull();
    expect(screen.queryByText(/主队门将/)).toBeNull();
    expect(container.querySelectorAll('[data-testid="player-avatar-image"] img').length).toBe(1);
  });

  it("双方都没有站位坐标时整个组件不渲染", () => {
    const home = team(true, [player({ player_id: "h1", pitch_x: null, pitch_y: null })]);
    const away = team(false, [player({ player_id: "a1", pitch_x: null, pitch_y: null })]);
    const { container } = render(<PitchFormation home={home} away={away} />);
    expect(container.firstChild).toBeNull();
  });

  it("头像加载失败时回退成球衣号文字,不留裂图标", () => {
    const home = team(true, [
      player({ player_id: "h1", name: "主队门将", shirt_number: "1", pitch_x: 0.1, pitch_y: 0.5 }),
    ]);
    const away = team(false, [
      player({ player_id: "a1", name: "客队门将", pitch_x: 0.1, pitch_y: 0.5 }),
    ]);
    const { container } = render(<PitchFormation home={home} away={away} />);
    const img = container.querySelector('[data-testid="player-avatar-image"] img')!;
    fireEvent.error(img);
    expect(container.querySelector('[data-testid="player-avatar-fallback"]')).not.toBeNull();
  });
});
