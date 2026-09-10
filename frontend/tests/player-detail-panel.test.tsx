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

/* ── 2026-09-10:球员卡改造(核心组 / 身份行 / 亮点句 / 射门摘要)────────
 * 对照 FotMob 安卓包 236.17398 的球员卡信息层级。这批断言守的是
 * "多出来的内容不能凭空捏造":身份信息拿不到就不渲染、射门摘要与射门图
 * 同源且随 player_id 变化、缺失 xG 不当 0。 */

type LineupTeam = MatchReport["lineups"][number];
type Shot = MatchReport["shots"][number];

function lineupTeam(starters: Partial<LineupTeam["starters"][number]>[]): LineupTeam {
  return {
    team_id: 1,
    is_home: true,
    formation: "4-3-3",
    starters: starters as LineupTeam["starters"],
    bench: [],
  } as LineupTeam;
}

function shot(o: Partial<Shot>): Shot {
  return { player_id: "1", team_id: 1, is_home: true, ...o } as Shot;
}

describe("球员卡:核心组", () => {
  it("渲染此前从未下发的 Top stats 字段", () => {
    const p = outfielder({
      expected_goals_on_target: 0.18,
      xg_and_xa: 0.22,
      big_chance_created: 1,
    });
    render(<MatchStatsSection {...BASE_PROPS} playerStats={[p]} />);
    fireEvent.click(screen.getByRole("button", { name: /测试球员/ }));
    expect(screen.getByText("核心")).not.toBeNull();
    expect(screen.getByText("xGOT")).not.toBeNull();
    expect(screen.getByText("0.18")).not.toBeNull();
    expect(screen.getByText("xG+xA")).not.toBeNull();
  });

  it("核心组不重复表格已有的列(同一数字不在一屏出现两次)", () => {
    const p = outfielder({ goals: 1, assists: 1, chances_created: 4, shots_on_target: 1 });
    render(<MatchStatsSection {...BASE_PROPS} playerStats={[p]} />);
    fireEvent.click(screen.getByRole("button", { name: /测试球员/ }));
    // 表头里各有一个,展开面板里不该再出现第二个
    expect(screen.getAllByText("创造机会")).toHaveLength(1);
    expect(screen.getAllByText("射正")).toHaveLength(1);
  });
});

describe("球员卡:身份行", () => {
  it("有阵容数据时显示号码/位置/换人时间/最佳标", () => {
    const p = outfielder({ touches: 30 });
    const lineups = [
      lineupTeam([
        {
          player_id: "1", name: "测试球员", shirt_number: "8", position_group: "MID",
          is_captain: true, is_player_of_the_match: true, sub_out_time: 76,
        },
      ]),
    ];
    render(<MatchStatsSection {...BASE_PROPS} playerStats={[p]} lineups={lineups} />);
    fireEvent.click(screen.getByRole("button", { name: /测试球员/ }));
    expect(screen.getByText("全场最佳")).not.toBeNull();
    expect(screen.getByText(/中场/)).not.toBeNull();
    expect(screen.getByText(/队长/)).not.toBeNull();
    expect(screen.getByText(/76' 被换下/)).not.toBeNull();
  });

  it("没有阵容数据时不渲染身份细节,也不用「—」占位", () => {
    const p = outfielder({ touches: 30 });
    render(<MatchStatsSection {...BASE_PROPS} playerStats={[p]} />);
    fireEvent.click(screen.getByRole("button", { name: /测试球员/ }));
    expect(screen.queryByText("全场最佳")).toBeNull();
    expect(screen.queryByText(/队长/)).toBeNull();
  });
});

describe("球员卡:射门摘要", () => {
  it("数字来自 report.shots 且随 player_id 过滤变化", () => {
    // 射正取官方球员统计(shots_on_target),不从射门图逐脚推
    const mine = outfielder({ player_id: "1", name: "射手甲", touches: 30, shots_on_target: 1 });
    const other = outfielder({ player_id: "2", name: "射手乙", touches: 30, shots_on_target: 1 });
    const shots = [
      shot({ player_id: "1", outcome: "Goal", xg: 0.3 }),
      shot({ player_id: "1", outcome: "Miss", xg: 0.2 }),
      shot({ player_id: "2", outcome: "Goal", xg: 0.9 }),
    ];
    render(<MatchStatsSection {...BASE_PROPS} playerStats={[mine, other]} shots={shots} />);

    fireEvent.click(screen.getByRole("button", { name: /射手甲/ }));
    expect(screen.getByText("射门 2 次 · 射正 1 · xG 合计 0.50")).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /射手乙/ }));
    expect(screen.getByText("射门 1 次 · 射正 1 · xG 合计 0.90")).not.toBeNull();
  });

  it("官方射正缺失时不写这一项(不用射门图逐脚推补)", () => {
    const p = outfielder({ player_id: "1", touches: 30 });
    const shots = [shot({ outcome: "Goal", xg: 0.4 })];
    render(<MatchStatsSection {...BASE_PROPS} playerStats={[p]} shots={shots} />);
    fireEvent.click(screen.getByRole("button", { name: /测试球员/ }));
    // "射正" 三个字在表头也有,所以断言摘要行本身的形状
    expect(screen.getByText("射门 1 次 · xG 合计 0.40")).not.toBeNull();
    expect(screen.queryByText(/射正 \d/)).toBeNull();
  });

  it("部分射门缺 xG 时如实标注分母,不把缺失当 0", () => {
    const p = outfielder({ player_id: "1", touches: 30 });
    const shots = [shot({ xg: 0.4, outcome: "Goal" }), shot({ xg: null, outcome: "Miss" })];
    render(<MatchStatsSection {...BASE_PROPS} playerStats={[p]} shots={shots} />);
    fireEvent.click(screen.getByRole("button", { name: /测试球员/ }));
    expect(screen.getByText(/1\/2 脚有 xG/)).not.toBeNull();
  });

  it("该球员没有射门时整行不渲染", () => {
    const p = outfielder({ player_id: "1", touches: 30 });
    render(<MatchStatsSection {...BASE_PROPS} playerStats={[p]} shots={[]} />);
    fireEvent.click(screen.getByRole("button", { name: /测试球员/ }));
    expect(screen.queryByText(/射门 /)).toBeNull();
  });
});

describe("球员卡:亮点句", () => {
  it("池子够大且全场最多时出句", () => {
    const players = [
      outfielder({ player_id: "1", name: "核心球员", touches: 95 }),
      ...Array.from({ length: 8 }, (_, i) =>
        outfielder({ player_id: `x${i}`, name: `路人${i}`, touches: 30 })
      ),
    ];
    render(<MatchStatsSection {...BASE_PROPS} playerStats={players} />);
    fireEvent.click(screen.getByRole("button", { name: /核心球员/ }));
    expect(screen.getByText("全场触球最多（95）")).not.toBeNull();
  });

  it("算不出亮点时整块不渲染(不写「暂无亮点」)", () => {
    const p = outfielder({ touches: 10 });
    render(<MatchStatsSection {...BASE_PROPS} playerStats={[p]} />);
    fireEvent.click(screen.getByRole("button", { name: /测试球员/ }));
    expect(screen.queryByText(/最多/)).toBeNull();
    expect(screen.queryByText(/暂无亮点/)).toBeNull();
  });
});
