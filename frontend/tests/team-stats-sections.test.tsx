/**
 * 球队数据榜的语义分区(2026-09-11)。
 *
 * 改造前这一页按**数据出处**分两坨(我们自己聚合的 5 个指标 + 一块叫「更多
 * 球队数据」的来源榜),改成按 重点数据 / 进攻 / 防守 / 纪律 分区。这里盯三
 * 件最容易错、且错了肉眼不一定看得出来的事:
 *
 * 1. 来源榜的分区必须来自后端透传的 category,不是前端另抄一份映射;
 * 2. 失球类指标必须升序——降序会把防守最差的队摆在第 1 名、还给它一个队色
 *    胶囊,读起来像在表扬它;
 * 3. 空分区整段不渲染(老赛季没采过来源榜时,防守/纪律可能整段没有内容)。
 */

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { TeamStatsSections } from "@/components/league/TeamStatsSections";
import type { TeamSeasonStatRow, TeamSourceBoard } from "@/lib/api-v1";

afterEach(cleanup);

function team(id: number, name: string) {
  return { team_id: id, name, name_en: name, crest_url: null };
}

function row(name: string, over: Partial<TeamSeasonStatRow> = {}): TeamSeasonStatRow {
  return {
    team: team(name.length, name),
    matches_played: 10,
    avg_possession: 50,
    avg_total_shots: 12,
    ...over,
  } as TeamSeasonStatRow;
}

function board(over: Partial<TeamSourceBoard> = {}): TeamSourceBoard {
  return {
    stat_name: "interception_team",
    label_zh: "拦截",
    stat_title: "Interceptions per match",
    per_match: true,
    stat_format: "fraction",
    stat_decimals: 1,
    category: "Defending",
    entries: [{ team: team(1, "布莱顿"), rank: 1, value: 9.4 }],
    ...over,
  } as TeamSourceBoard;
}

function sectionNamed(title: string) {
  const h = screen.getByText(title);
  return h.closest("section")!;
}

describe("TeamStatsSections", () => {
  it("按 重点数据/进攻/防守/纪律 分区,顺序与 FotMob 一致", () => {
    const { container } = render(
      <TeamStatsSections
        rows={[row("阿森纳", { avg_fouls: 9.1, avg_expected_goals_conceded: 0.8 })]}
        boards={[board()]}
      />
    );
    const titles = [...container.querySelectorAll("h2")].map((h) => h.textContent);
    expect(titles).toEqual(["重点数据", "进攻", "防守", "纪律"]);
  });

  it("来源榜按后端下发的 category 落区,不是前端猜的", () => {
    render(
      <TeamStatsSections
        rows={[]}
        boards={[
          board({ stat_name: "big_chance_team", label_zh: "创造绝佳机会", category: "Attacking", per_match: false }),
          board({ stat_name: "saves_team", label_zh: "扑救", category: "Defending" }),
          board({ stat_name: "total_yel_card_team", label_zh: "黄牌", category: "Discipline", per_match: false }),
        ]}
      />
    );
    expect(within(sectionNamed("进攻")).getByText(/创造绝佳机会/)).toBeTruthy();
    expect(within(sectionNamed("防守")).getByText(/扑救/)).toBeTruthy();
    expect(within(sectionNamed("纪律")).getByText(/黄牌/)).toBeTruthy();
  });

  it("同一张榜换个 category 就换一区(证明真的读了字段)", () => {
    const { unmount } = render(
      <TeamStatsSections rows={[]} boards={[board({ category: "Attacking" })]} />
    );
    expect(within(sectionNamed("进攻")).getByText(/拦截/)).toBeTruthy();
    unmount();
    render(<TeamStatsSections rows={[]} boards={[board({ category: "Defending" })]} />);
    expect(within(sectionNamed("防守")).getByText(/拦截/)).toBeTruthy();
  });

  it("被创造 xG 升序:防得最好的(数值最小)排第 1", () => {
    render(
      <TeamStatsSections
        rows={[
          row("漏勺队", { avg_expected_goals_conceded: 2.4 }),
          row("铁桶队", { avg_expected_goals_conceded: 0.6 }),
        ]}
        boards={[]}
      />
    );
    const card = sectionNamed("防守").querySelector("li")!;
    expect(card.textContent).toContain("铁桶队");
    expect(card.textContent).toContain("0.60");
  });

  it("场均进球类指标仍是降序:进得最多的排第 1", () => {
    render(
      <TeamStatsSections
        rows={[
          row("铁桶队", { avg_total_shots: 6.2 }),
          row("火力队", { avg_total_shots: 18.9 }),
        ]}
        boards={[]}
      />
    );
    const card = sectionNamed("进攻").querySelector("li")!;
    expect(card.textContent).toContain("火力队");
  });

  it("空分区整段不渲染(老赛季没采过来源榜)", () => {
    const { container } = render(
      <TeamStatsSections rows={[row("阿森纳")]} boards={[]} />
    );
    const titles = [...container.querySelectorAll("h2")].map((h) => h.textContent);
    expect(titles).toEqual(["重点数据", "进攻"]); // 无防守/纪律数据
  });

  it("全空时什么都不渲染,不摆空卡片墙", () => {
    const { container } = render(<TeamStatsSections rows={[]} boards={[]} />);
    expect(container.innerHTML).toBe("");
  });

  it("补齐了此前一张卡都没渲染过的 DTO 字段(角球/零封/犯规/牌)", () => {
    render(
      <TeamStatsSections
        rows={[
          row("阿森纳", {
            avg_corners: 6.3,
            clean_sheets: 9,
            avg_fouls: 9.1,
            avg_yellow_cards: 1.8,
            avg_red_cards: 0.1,
          }),
        ]}
        boards={[]}
      />
    );
    for (const t of ["场均角球", "零封场次", "场均犯规", "场均黄牌", "场均红牌"]) {
      expect(screen.getByText(t)).toBeTruthy();
    }
  });

  it("重点数据里进球/失球排在控球率之前(对齐 FotMob 的阅读顺序)", () => {
    render(
      <TeamStatsSections
        rows={[row("阿森纳", { avg_possession: 55, clean_sheets: 9 })]}
        boards={[
          board({ stat_name: "goals_team_match", label_zh: "场均进球", category: "Top Stat" }),
          board({ stat_name: "goals_conceded_team_match", label_zh: "场均失球", category: "Top Stat" }),
        ]}
      />
    );
    const titles = [...sectionNamed("重点数据").querySelectorAll('[class*="title"]')].map(
      (t) => t.textContent
    );
    expect(titles).toEqual(["场均进球 · 场均", "场均失球 · 场均", "控球率", "零封场次"]);
  });
});
