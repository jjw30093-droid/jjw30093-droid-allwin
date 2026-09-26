/**
 * 积分榜(2026-09-26 手机端改造):
 * - "冠军"只在赛季结束后出现(season_finished),进行中榜首不得有任何"冠军"字样;
 * - 冠军是单独一种颜色的图例,只在赛季结束后出现;进行中只有资格区图例;
 * - 手机(<768px)列顺序 名次/球队/积分/场次/净胜 在前,靠积分/净胜各渲染两份、
 *   CSS 按断点切换实现——这里守住两份都在、桌面原顺序不变。
 */

import { cleanup, render, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { StandingsTable } from "@/components/league/StandingsTable";
import type { StandingRow } from "@/lib/api-v1";

afterEach(cleanup);

function row(over: Partial<StandingRow> & { position: number; name: string }): StandingRow {
  const { name, ...rest } = over;
  return {
    Team_ID: over.position,
    Team_Name: name,
    played: 5,
    wins: 3,
    draws: 1,
    losses: 1,
    goals_for: 9,
    goals_against: 4,
    goal_diff: 5,
    points: 10,
    qual_color: null,
    group_name: null,
    team: { team_id: over.position, name, name_en: null, crest_url: null },
    ...rest,
  } as StandingRow;
}

const ROWS = [
  row({ position: 1, name: "甲队", qual_color: "#2AD572" }),
  row({ position: 2, name: "乙队", qual_color: "#2AD572", points: 9 }),
  row({ position: 3, name: "丙队", qual_color: "#0046A7", points: 8 }),
  row({ position: 4, name: "丁队", qual_color: "#FF4646", points: 3 }),
];

describe("冠军只在赛季结束后出现", () => {
  it("赛季进行中(默认):页面上没有任何'冠军'字样,图例也没有", () => {
    const { container } = render(<StandingsTable rows={ROWS} />);
    expect(container.textContent).not.toContain("冠军");
  });

  it("seasonFinished=false 显式传入同样没有", () => {
    const { container } = render(<StandingsTable rows={ROWS} seasonFinished={false} />);
    expect(container.textContent).not.toContain("冠军");
  });

  it("赛季结束:榜首行有'冠军'标签,图例有单独一项'冠军'", () => {
    const { container } = render(<StandingsTable rows={ROWS} seasonFinished />);
    // 标签 + 图例各一处
    expect(container.textContent!.match(/冠军/g)).toHaveLength(2);
    const [first, second] = [...container.querySelectorAll("tbody tr")] as HTMLElement[];
    expect(within(first).getByText("冠军")).toBeTruthy();
    expect(within(second).queryByText("冠军")).toBeNull();
  });

  it("分组赛制(group_name 非空)各组第一不是联赛冠军,赛季结束也不标", () => {
    const grouped = ROWS.map((r) => ({ ...r, group_name: "East" }));
    const { container } = render(<StandingsTable rows={grouped} seasonFinished />);
    expect(container.textContent).not.toContain("冠军");
  });
});

describe("图例", () => {
  it("进行中只显示资格区图例(按联赛配置),不再把绿色叫'冠军 / 欧战资格'", () => {
    const { container } = render(<StandingsTable rows={ROWS} leagueId={47} />);
    const text = container.textContent!;
    expect(text).toContain("欧冠区");
    expect(text).toContain("欧联区");
    expect(text).toContain("降级区");
    expect(text).not.toContain("冠军");
    expect(text).not.toContain("升级区");
  });

  it("同一个绿色在不同联赛文字不同:英冠是直接升级区,巴甲是解放者杯小组赛区", () => {
    const r = [row({ position: 1, name: "甲队", qual_color: "#2AD572" })];
    expect(render(<StandingsTable rows={r} leagueId={48} />).container.textContent).toContain("直接升级区");
    cleanup();
    expect(render(<StandingsTable rows={r} leagueId={268} />).container.textContent).toContain("解放者杯小组赛区");
  });

  it("未配置的联赛回落'晋级区'", () => {
    const { container } = render(<StandingsTable rows={ROWS} leagueId={999999} />);
    expect(container.textContent).toContain("晋级区");
    expect(container.textContent).not.toContain("欧冠区");
  });

  it("没有任何资格色且赛季进行中时不渲染图例", () => {
    const plain = ROWS.map((r) => ({ ...r, qual_color: null }));
    const { container } = render(<StandingsTable rows={plain} leagueId={47} />);
    expect(container.textContent).not.toContain("降级区");
  });
});

describe("列结构", () => {
  it("表头 DOM 顺序:手机顺序在前(名次 球队 积分 场次 净胜),桌面原顺序在后(…净胜 积分)", () => {
    const { container } = render(<StandingsTable rows={ROWS} />);
    const heads = [...container.querySelectorAll("thead th")].map((th) => th.textContent);
    expect(heads).toEqual(["名次", "球队", "积分", "场次", "净胜", "胜", "平", "负", "进球", "失球", "净胜", "积分"]);
  });

  it("每一行的数据单元格数与表头一致(积分/净胜两份数值相同)", () => {
    const { container } = render(<StandingsTable rows={ROWS} />);
    const tr = container.querySelector("tbody tr")!;
    const cells = [...tr.querySelectorAll("td")].map((td) => td.textContent);
    expect(cells).toHaveLength(12);
    expect(cells[2]).toBe(cells[11]); // 积分
    expect(cells[4]).toBe(cells[10]); // 净胜
    expect(cells[4]).toBe("+5");
  });
});
