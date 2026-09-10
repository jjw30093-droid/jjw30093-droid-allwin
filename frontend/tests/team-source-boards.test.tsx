/**
 * 来源方赛季球队榜(TeamSourceBoards,2026-09-10)。
 *
 * 这一组榜与同页上方"我们自己算的"榜是两套口径,核心风险是**单位标错**:
 * 同一批榜里 poss_won_att_3rd_team 是场均、big_chance_team 是赛季合计,而两者
 * stat_format 都可能是 'fraction'。单位必须来自后端下发的 per_match,
 * 前端不许按字段名猜。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  TeamSourceBoards,
  boardTitle,
  formatBoardValue,
} from "@/components/league/TeamSourceBoards";
import type { TeamSourceBoard } from "@/lib/api-v1";

afterEach(cleanup);

function board(overrides: Partial<TeamSourceBoard> = {}): TeamSourceBoard {
  return {
    stat_name: "poss_won_att_3rd_team",
    label_zh: "前场反抢",
    stat_title: "Possession won final 3rd per match",
    per_match: true,
    stat_format: "fraction",
    stat_decimals: 1,
    entries: [
      {
        team: { team_id: 10204, name: "布莱顿", name_en: "Brighton", crest_url: null },
        rank: 1,
        value: 5.1,
      },
    ],
    ...overrides,
  } as TeamSourceBoard;
}

describe("formatBoardValue", () => {
  it("按来源自报的格式与小数位渲染", () => {
    expect(formatBoardValue(5.1, "fraction", 1)).toBe("5.1");
    expect(formatBoardValue(120, "number", 0)).toBe("120");
    expect(formatBoardValue(60.53, "percent", 1)).toBe("60.5%");
  });

  it("跑动距离按公里显示(来源给的是米)", () => {
    expect(formatBoardValue(118540.1, "meter", 1)).toBe("118.5 km");
  });

  it("缺失值返回 null,不当 0", () => {
    expect(formatBoardValue(null, "fraction", 1)).toBeNull();
    expect(formatBoardValue(undefined, "number", 0)).toBeNull();
    expect(formatBoardValue(Number.NaN, "number", 0)).toBeNull();
  });
});

describe("boardTitle", () => {
  it("场均与赛季合计分别标注", () => {
    expect(boardTitle(board({ per_match: true }))).toBe("前场反抢 · 场均");
    expect(
      boardTitle(board({ stat_name: "big_chance_team", label_zh: "创造绝佳机会", per_match: false }))
    ).toBe("创造绝佳机会 · 赛季合计");
  });

  it("来源没给标题时不猜单位", () => {
    expect(boardTitle(board({ per_match: null, stat_title: null }))).toBe("前场反抢");
  });
});

describe("TeamSourceBoards", () => {
  it("渲染榜单与数值", () => {
    render(<TeamSourceBoards boards={[board()]} />);
    expect(screen.getByText("前场反抢 · 场均")).toBeTruthy();
    expect(screen.getByText("布莱顿")).toBeTruthy();
    expect(screen.getByText("5.1")).toBeTruthy();
  });

  it("没有任何榜单时整块不渲染(不摆空卡片墙)", () => {
    const { container } = render(<TeamSourceBoards boards={[]} />);
    expect(container.innerHTML).toBe("");
  });

  it("榜单存在但全部行无数值时同样不渲染", () => {
    const empty = board({
      entries: [
        {
          team: { team_id: 1, name: "某队", name_en: "X", crest_url: null },
          rank: 1,
          value: null,
        },
      ],
    } as Partial<TeamSourceBoard>);
    const { container } = render(<TeamSourceBoards boards={[empty]} />);
    expect(container.innerHTML).toBe("");
  });
});
