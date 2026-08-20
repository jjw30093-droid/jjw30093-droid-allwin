/**
 * MatchEventsSection:已完赛比赛「事件」tab 的赛事时间线(2026-08-21)。
 *
 * 覆盖 2026-08 QA 抽查发现的两个 bug:
 * - 终场分隔行此前硬编码显示"半场",与页头"全场"比分矛盾;
 * - 乌龙球没有任何标注,球员名+对方队名的组合容易被误读成数据错误。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MatchEventsSection } from "@/components/matches/MatchEventsSection";
import type { MatchReportResponse } from "@/lib/api-v1";

afterEach(cleanup);

type Event = Extract<MatchReportResponse, { available: true }>["events"][number];

function event(overrides: Partial<Event> = {}): Event {
  return {
    event_index: 0,
    event_type: "Goal",
    minute: 1,
    is_added_time: false,
    minutes_added: null,
    is_home: true,
    home_score: 1,
    away_score: 0,
    player_name: "球员A",
    card_type: null,
    assist_player_name: null,
    sub_in_player_name: null,
    sub_out_player_name: null,
    half_kind: null,
    is_own_goal: false,
    ...overrides,
  };
}

describe("MatchEventsSection", () => {
  it("FT(全场结束)渲染「全场」,不出现「半场」——本 bug 的核心回归断言", () => {
    render(
      <MatchEventsSection
        events={[
          event({
            event_index: 1,
            event_type: "Half",
            minute: 90,
            is_home: null,
            home_score: 2,
            away_score: 1,
            player_name: null,
            half_kind: "FT",
          }),
        ]}
        homeName="主队"
        awayName="客队"
      />,
    );
    expect(screen.getByText("全场 2–1")).not.toBeNull();
    expect(screen.queryByText(/半场/)).toBeNull();
  });

  it("HT(中场)渲染「中场」", () => {
    render(
      <MatchEventsSection
        events={[
          event({
            event_index: 1,
            event_type: "Half",
            minute: 45,
            is_home: null,
            home_score: 1,
            away_score: 0,
            player_name: null,
            half_kind: "HT",
          }),
        ]}
        homeName="主队"
        awayName="客队"
      />,
    );
    expect(screen.getByText("中场 1–0")).not.toBeNull();
  });

  it("AET(加时赛结束)渲染「加时赛结束」", () => {
    render(
      <MatchEventsSection
        events={[
          event({
            event_index: 1,
            event_type: "Half",
            minute: 120,
            is_home: null,
            home_score: 3,
            away_score: 2,
            player_name: null,
            half_kind: "AET",
          }),
        ]}
        homeName="主队"
        awayName="客队"
      />,
    );
    expect(screen.getByText("加时赛结束 3–2")).not.toBeNull();
  });

  it("half_kind 为 null 的 Half 事件退回中性的「阶段结束」,不再猜是「半场」", () => {
    render(
      <MatchEventsSection
        events={[
          event({
            event_index: 1,
            event_type: "Half",
            minute: 45,
            is_home: null,
            home_score: null,
            away_score: null,
            player_name: null,
            half_kind: null,
          }),
        ]}
        homeName="主队"
        awayName="客队"
      />,
    );
    expect(screen.getByText("阶段结束")).not.toBeNull();
    expect(screen.queryByText(/半场/)).toBeNull();
  });

  it("乌龙球进球文本带「(乌龙球)」标注,且页面出现归队说明脚注", () => {
    render(
      <MatchEventsSection
        events={[
          event({
            event_index: 1,
            player_name: "客队球员",
            is_home: true, // 受益方是主队,球员却是客队的——语义上如此
            is_own_goal: true,
          }),
        ]}
        homeName="主队"
        awayName="客队"
      />,
    );
    expect(screen.getByText(/客队球员\s*\(乌龙球\)/)).not.toBeNull();
    expect(screen.getByText(/乌龙球按受益方/)).not.toBeNull();
  });

  it("普通进球不带乌龙球标注,也没有归队脚注", () => {
    render(
      <MatchEventsSection
        events={[event({ event_index: 1, player_name: "球员A", is_own_goal: false })]}
        homeName="主队"
        awayName="客队"
      />,
    );
    expect(screen.getByText("球员A")).not.toBeNull();
    expect(screen.queryByText(/乌龙球/)).toBeNull();
  });

  it("乌龙球没有助攻标注(assist 恒为 null,与真实数据一致)", () => {
    render(
      <MatchEventsSection
        events={[
          event({
            event_index: 1,
            player_name: "客队球员",
            is_home: true,
            is_own_goal: true,
            assist_player_name: null,
          }),
        ]}
        homeName="主队"
        awayName="客队"
      />,
    );
    expect(screen.queryByText(/助攻/)).toBeNull();
  });
});
