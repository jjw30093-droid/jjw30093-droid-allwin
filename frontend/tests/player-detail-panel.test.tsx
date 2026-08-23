/**
 * MatchStatsSection 球员详情展开行(2026-08-23 对照 FotMob 官方安卓包)。
 * 覆盖:点击展开/收起、门将 vs 外场分组差异、分数式字段(37/40)、
 * 体能"有则显示无则不显示"。
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MatchStatsSection } from "@/components/matches/MatchStatsSection";
import type { MatchReportResponse } from "@/lib/api-v1";

afterEach(cleanup);

type MatchReport = Extract<MatchReportResponse, { available: true }>;
type PlayerStat = MatchReport["player_stats"][number];

function outfielder(overrides: Partial<PlayerStat>): PlayerStat {
  return {
    player_id: "1",
    name: "测试球员",
    team_id: 1,
    is_home: true,
    is_goalkeeper: false,
    minutes_played: 90,
    ...overrides,
  } as PlayerStat;
}

function keeper(overrides: Partial<PlayerStat>): PlayerStat {
  return {
    player_id: "99",
    name: "测试门将",
    team_id: 1,
    is_home: true,
    is_goalkeeper: true,
    minutes_played: 90,
    ...overrides,
  } as PlayerStat;
}

const BASE_PROPS = {
  teamStats: [],
  shots: [],
  homeName: "主队",
  awayName: "客队",
};

describe("球员详情展开行", () => {
  it("默认收起,点击后展开分组详情,再点一次收起", () => {
    const p = outfielder({ touches: 45, recoveries: 3 });
    render(<MatchStatsSection {...BASE_PROPS} playerStats={[p]} />);
    const row = screen.getByRole("button", { name: /测试球员/ });
    expect(row.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("触球")).toBeNull();

    fireEvent.click(row);
    expect(row.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("触球")).not.toBeNull();
    expect(screen.getByText("45")).not.toBeNull();

    fireEvent.click(row);
    expect(row.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("触球")).toBeNull();
  });

  it("外场球员不显示门将组,门将不显示进攻/防守/对抗组", () => {
    const out = outfielder({ touches: 20, duel_won: 5 });
    const gk = keeper({ saves: 4, goals_conceded: 1 });
    render(<MatchStatsSection {...BASE_PROPS} playerStats={[out, gk]} />);

    fireEvent.click(screen.getByRole("button", { name: /测试球员/ }));
    expect(screen.getByText("对抗")).not.toBeNull();
    expect(screen.queryByText("门将")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /测试门将/ }));
    expect(screen.getByText("门将")).not.toBeNull();
    expect(screen.queryByText("对抗")).toBeNull();
  });

  it("成功传球带分母,渲染成 37/40 而不是裸数字", () => {
    const p = outfielder({ accurate_passes: 37, accurate_passes_total: 40 });
    render(<MatchStatsSection {...BASE_PROPS} playerStats={[p]} />);
    fireEvent.click(screen.getByRole("button", { name: /测试球员/ }));
    expect(screen.getByText("37/40")).not.toBeNull();
  });

  it("有分子无分母时退回裸数字,不编造分母", () => {
    const p = outfielder({ accurate_passes: 37, accurate_passes_total: null });
    render(<MatchStatsSection {...BASE_PROPS} playerStats={[p]} />);
    fireEvent.click(screen.getByRole("button", { name: /测试球员/ }));
    expect(screen.getByText("37")).not.toBeNull();
    expect(screen.queryByText(/37\//)).toBeNull();
  });

  it("体能数据全空时不渲染体能组(有则显示、无则不显示)", () => {
    const p = outfielder({ touches: 10 });
    render(<MatchStatsSection {...BASE_PROPS} playerStats={[p]} />);
    fireEvent.click(screen.getByRole("button", { name: /测试球员/ }));
    expect(screen.queryByText("体能")).toBeNull();
  });

  it("体能数据存在时渲染体能组", () => {
    const p = outfielder({ physical_metrics_distance_covered: 10442, physical_metrics_topspeed: 29.9 });
    render(<MatchStatsSection {...BASE_PROPS} playerStats={[p]} />);
    fireEvent.click(screen.getByRole("button", { name: /测试球员/ }));
    expect(screen.getByText("体能")).not.toBeNull();
    expect(screen.getByText("10442m")).not.toBeNull();
    expect(screen.getByText("29.9km/h")).not.toBeNull();
  });

  it("完全没有任何分组数据时展开显示空态,不崩溃", () => {
    const p = outfielder({});
    render(<MatchStatsSection {...BASE_PROPS} playerStats={[p]} />);
    fireEvent.click(screen.getByRole("button", { name: /测试球员/ }));
    expect(screen.getByText("暂无更多分组数据。")).not.toBeNull();
  });
});
