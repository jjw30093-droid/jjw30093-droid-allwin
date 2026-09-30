/**
 * 选人面板(参照 FotMob Lineup Builder):点位置开面板 → 搜索过滤 → 选中后阵容变化 → Esc 关闭。
 * 只测交互与状态,不测样式;dnd-kit 在 jsdom 里不真的拖。
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LineupPitchCard } from "@/app/simulator/LineupPitchCard";
import type { SlotAssign, TeamSetup } from "@/features/simulator/engine";
import type { PlayerParams, PosGroup, SimParams } from "@/features/simulator/types";

afterEach(cleanup);

const SLOTS: [number, PosGroup, number, number][] = [
  [11, "GK", 0.5, 0.1], [32, "FB", 0.12, 0.29], [34, "CB", 0.38, 0.29], [36, "CB", 0.62, 0.29], [38, "FB", 0.88, 0.29],
  [64, "DM", 0.3, 0.48], [66, "DM", 0.7, 0.48], [83, "W", 0.16, 0.68], [85, "AM", 0.5, 0.68], [87, "W", 0.84, 0.68], [115, "ST", 0.5, 0.87],
];
function player(id: string, main: PosGroup, name: string, minutes = 900): PlayerParams {
  return { player_id: id, name_zh: name, name_en: null, shirt_number: id.replace(/\D/g, "") || "9", team_id: 10, main_position: main, minutes, npxg90: 0.1, a_p: 0.1, r_p: 5, g_p: main === "GK" ? 0 : null, penalties_taken: 0, penalties_scored: 0, starts: 5 } as unknown as PlayerParams;
}
function params(): SimParams {
  const players: Record<string, PlayerParams> = {};
  SLOTS.forEach(([, g], i) => (players[`p${i}`] = player(`p${i}`, g, `首发${i}`)));
  players.b1 = player("b1", "ST", "替补中锋", 700);
  players.b2 = player("b2", "CB", "替补中卫", 500);
  return {
    meta: { generated_at: "x", model_version: "v0.3", uncalibrated: true },
    leagues: { "1": {} },
    formations: { "4-2-3-1": { samples: 10, slots: SLOTS.map(([pid, , x, y]) => ({ position_id: pid, x, y })) } },
    position_map: { "4-2-3-1": Object.fromEntries(SLOTS.map(([pid, g]) => [String(pid), g])) },
    teams: { "10": { team_id: 10, league_id: 1, name_zh: "甲队", squad: [...Object.keys(players)], last_lineup: { match_id: 1, date: "2026-09-19", formation: "4-2-3-1", starters: SLOTS.map(([pid], i) => ({ player_id: `p${i}`, position_id: pid })) } } },
    players,
    fixtures: {},
  } as unknown as SimParams;
}
function setup(): TeamSetup {
  const slots: SlotAssign[] = SLOTS.map(([pid, g, x, y], i) => ({ positionId: pid, group: g, playerId: `p${i}`, x, y }));
  return { teamId: 10, formation: "4-2-3-1", slots, focuses: [], shortRest: false };
}

function renderCard(onChange = vi.fn()) {
  const p = params();
  const s = setup();
  render(
    <LineupPitchCard
      params={p}
      sideLabel="主队"
      setup={s}
      teams={Object.values(p.teams)}
      disabledTeamId={null}
      badge="position"
      onBadge={() => {}}
      flaggedSlots={[]}
      onChange={onChange}
      onTeamChange={() => {}}
    />,
  );
  return { p, s, onChange };
}

describe("选人面板", () => {
  it("点中锋位置打开面板:标题带位置,推荐球员是同位置的替补,当前球员不在列表里", () => {
    renderCard();
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByTestId("slot-10"));
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-label")).toContain("中锋");
    expect(screen.getByTestId("pick-b1")).toBeTruthy();
    expect(screen.queryByTestId("pick-p10")).toBeNull();
    expect(screen.queryByText("移除球员")).toBeNull();
  });

  it("搜索过滤到一个人;选中后 onChange 收到换上的阵容并关闭面板", () => {
    const { onChange } = renderCard();
    fireEvent.click(screen.getByTestId("slot-10"));
    fireEvent.change(screen.getByLabelText("搜索球员"), { target: { value: "替补中卫" } });
    expect(screen.queryByTestId("pick-b1")).toBeNull();
    fireEvent.click(screen.getByTestId("pick-b2"));
    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0][0] as TeamSetup;
    expect(next.slots[10].playerId).toBe("b2");
    expect(next.slots.filter((x) => x.playerId === "p10")).toHaveLength(0);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("选一名首发 → 互换位置", () => {
    const { onChange } = renderCard();
    fireEvent.click(screen.getByTestId("slot-10"));
    fireEvent.click(screen.getByTestId("pick-p8"));
    const next = onChange.mock.calls[0][0] as TeamSetup;
    expect(next.slots[10].playerId).toBe("p8");
    expect(next.slots[8].playerId).toBe("p10");
  });

  it("Esc 关闭面板,不改阵容", () => {
    const { onChange } = renderCard();
    fireEvent.click(screen.getByTestId("slot-2"));
    expect(screen.getByRole("dialog")).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("点队名打开选队面板", () => {
    renderCard();
    fireEvent.click(screen.getByTestId("team-pick-主队"));
    expect(screen.getByRole("dialog").getAttribute("aria-label")).toBe("选择球队");
    expect(screen.getByTestId("team-10")).toBeTruthy();
  });
});

describe("左右方向", () => {
  it("左后卫(38,FotMob x=0.88)在画面左侧,右后卫(32,x=0.12)在右侧", () => {
    renderCard();
    const left = (i: number) => parseFloat((screen.getByTestId(`slot-${i}`) as HTMLElement).style.left);
    expect(left(4)).toBeCloseTo(12, 5); // 38 左后卫
    expect(left(1)).toBeCloseTo(88, 5); // 32 右后卫
    expect(left(9)).toBeLessThan(50); // 87 左边锋(x 0.84)
    expect(left(7)).toBeGreaterThan(50); // 83 右边锋(x 0.16)
  });
});
