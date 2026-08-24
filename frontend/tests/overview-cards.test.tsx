/**
 * 总览新增三卡(2026-08-25):TopStatsCard(重点数据 5 项双向条)、
 * TopRatedCard(最高评分)、OverviewKeyEvents(关键事件精简时间线)。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { TopStatsCard } from "@/components/matches/TopStatsCard";
import { TopRatedCard } from "@/components/matches/TopRatedCard";
import { OverviewKeyEvents } from "@/components/matches/OverviewKeyEvents";
import { hasKeyEvents } from "@/components/matches/overviewKeyEvents.shared";
import type { MatchReportResponse } from "@/lib/api-v1";

afterEach(cleanup);

type MatchReport = Extract<MatchReportResponse, { available: true }>;
type TeamStat = MatchReport["team_stats"][number];
type MatchEvent = MatchReport["events"][number];

function teamStat(isHome: boolean, overrides: Partial<TeamStat> = {}): TeamStat {
  return {
    team_id: isHome ? 1 : 2,
    is_home: isHome,
    period: "All",
    possession: isHome ? 61 : 39,
    expected_goals: isHome ? 2.31 : 0.87,
    total_shots: isHome ? 14 : 5,
    shots_on_target: isHome ? 6 : 1,
    touches_opp_box: isHome ? 28 : 11,
    ...overrides,
  } as TeamStat;
}

function event(overrides: Partial<MatchEvent>): MatchEvent {
  return {
    event_index: 0,
    event_type: "Goal",
    minute: 24,
    is_added_time: false,
    minutes_added: null,
    is_home: true,
    home_score: 1,
    away_score: 0,
    player_name: "Test Striker",
    card_type: null,
    assist_player_name: null,
    sub_in_player_name: null,
    sub_out_player_name: null,
    half_kind: null,
    is_own_goal: false,
    ...overrides,
  } as MatchEvent;
}

describe("TopStatsCard(重点数据)", () => {
  it("FotMob 5 项按序渲染,数值来自两队统计行", () => {
    render(
      <TopStatsCard
        homeStat={teamStat(true)}
        awayStat={teamStat(false)}
        homeName="主队"
        awayName="客队"
      />,
    );
    const labels = screen
      .getAllByText(/控球率|官方统计 xG|射门次数|射正|对方禁区内触球/)
      .map((el) => el.textContent);
    expect(labels).toEqual([
      "控球率",
      "官方统计 xG",
      "射门次数",
      "射正",
      "对方禁区内触球",
    ]);
    expect(screen.getByText("61%")).not.toBeNull();
    expect(screen.getByText("2.31")).not.toBeNull();
  });

  it("两边都缺的项整行不渲染;5 项全缺整卡不渲染", () => {
    const { container, rerender } = render(
      <TopStatsCard
        homeStat={teamStat(true, { touches_opp_box: null })}
        awayStat={teamStat(false, { touches_opp_box: null })}
        homeName="主"
        awayName="客"
      />,
    );
    expect(screen.queryByText("对方禁区内触球")).toBeNull();

    rerender(
      <TopStatsCard homeStat={null} awayStat={null} homeName="主" awayName="客" />,
    );
    expect(container.querySelector('[data-testid="top-stats-card"]')).toBeNull();
  });

  it("单边缺失不画条(条说对方是 0、数字说未知是自相矛盾)", () => {
    const { container } = render(
      <TopStatsCard
        homeStat={teamStat(true, { possession: null })}
        awayStat={teamStat(false)}
        homeName="主"
        awayName="客"
      />,
    );
    // 控球率行存在(单边有值)但没有条;其余 4 行有条 → 共 4 个 track
    expect(screen.getByText("控球率")).not.toBeNull();
    expect(container.querySelectorAll('[class*="track"]').length).toBe(4);
  });
});

describe("TopRatedCard(最高评分)", () => {
  it("渲染头像+球衣号姓名+球队+评分胶囊", () => {
    const { container } = render(
      <TopRatedCard
        topRated={{
          player_id: "p100",
          name: "Test Striker",
          team_id: 1,
          is_home: true,
          rating: 7.7,
          shirt_number: "9",
        }}
        homeName="主队"
        awayName="客队"
      />,
    );
    expect(screen.getByText("9 Test Striker")).not.toBeNull();
    expect(screen.getByText("主队")).not.toBeNull();
    expect(screen.getByText("7.7")).not.toBeNull();
    expect(container.querySelector("img")!.getAttribute("src")).toContain(
      "playerimages/p100.png",
    );
  });
});

describe("OverviewKeyEvents(关键事件精简版)", () => {
  it("只列进球与红黄牌,换人/VAR/半场分隔不出现", () => {
    render(
      <OverviewKeyEvents
        events={[
          event({ event_index: 0, event_type: "Goal", player_name: "射手" }),
          event({
            event_index: 1,
            event_type: "Card",
            card_type: "Yellow",
            player_name: "染黄者",
            home_score: null,
            away_score: null,
          }),
          event({ event_index: 2, event_type: "Substitution", player_name: "换人者" }),
          event({ event_index: 3, event_type: "VAR", player_name: "VAR 对象" }),
          event({ event_index: 4, event_type: "Half", player_name: null }),
        ]}
      />,
    );
    expect(screen.getByText(/射手/)).not.toBeNull();
    expect(screen.getByText(/染黄者/)).not.toBeNull();
    expect(screen.getByText("黄牌")).not.toBeNull();
    expect(screen.queryByText(/换人者/)).toBeNull();
    expect(screen.queryByText(/VAR 对象/)).toBeNull();
  });

  it("乌龙球标注(乌龙),比分随进球显示", () => {
    render(
      <OverviewKeyEvents
        events={[
          event({
            event_type: "Goal",
            player_name: "Away Defender",
            is_own_goal: true,
            is_home: true, // 受益方(见 _events 注释:乌龙事件 is_home 指受益方)
            home_score: 2,
            away_score: 0,
          }),
        ]}
      />,
    );
    expect(screen.getByText("(乌龙)")).not.toBeNull();
    expect(screen.getByText("2–0")).not.toBeNull();
  });

  it("没有任何关键事件时返回 null;hasKeyEvents 供调用方隐藏整节", () => {
    const subsOnly = [event({ event_type: "Substitution" })];
    const { container } = render(<OverviewKeyEvents events={subsOnly} />);
    expect(container.firstChild).toBeNull();
    expect(hasKeyEvents(subsOnly)).toBe(false);
    expect(hasKeyEvents([event({})])).toBe(true);
  });

  it("不在 MatchTabs 树内(无切换 context)时不渲染「查看完整时间线」按钮", () => {
    render(<OverviewKeyEvents events={[event({})]} />);
    expect(screen.queryByText("查看完整时间线 →")).toBeNull();
  });
});
