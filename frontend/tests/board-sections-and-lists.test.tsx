/**
 * 榜单折叠 / 吸顶分组 / 历史战绩「加载更多」/ 比赛行只显示开球时间(2026-09-26)。
 */
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BoardSectionTabs } from "@/components/league/BoardSectionTabs";
import { boardSectionDomId } from "@/components/league/boardSections";
import { PLAYER_GROUP_ORDER, playerGroupOf } from "@/components/league/playerBoardGroups";
import { PlayerBoards } from "@/components/league/PlayerBoards";
import { LoadMoreList } from "@/components/reco/LoadMoreList";
import { MatchRow } from "@/components/matches/MatchRow";
import type { MatchSummary, PlayersResponse } from "@/lib/api-v1";

afterEach(cleanup);

// 线上球员榜 28 个 stat_name(2026-09-26 从 /api/v1/leagues/47/players 取得)
const LIVE_STATS = [
  "goals", "goal_assist", "expected_goals", "expected_goalsontarget", "rating", "expected_assists",
  "yellow_card", "red_card", "fouls", "total_tackle", "interception", "ball_recovery",
  "effective_clearance", "outfielder_block", "total_scoring_att", "ontarget_scoring_att",
  "big_chance_created", "big_chance_missed", "accurate_pass", "accurate_long_balls", "won_contest",
  "poss_won_att_3rd", "defensive_contributions", "penalty_won", "penalty_conceded", "saves",
  "clean_sheet", "goals_conceded",
];

describe("球员榜分组", () => {
  it("线上 28 个榜全部归到四个具名分组,没有落进「其他」", () => {
    expect(LIVE_STATS).toHaveLength(28);
    for (const s of LIVE_STATS) expect(playerGroupOf(s), s).not.toBe("other");
  });

  it("没登记过的新榜落进「其他」,不会消失", () => {
    expect(playerGroupOf("some_new_stat")).toBe("other");
  });

  it("负向榜(黄牌/红牌/犯规/送点)在纪律组;进球/助攻/评分在重点数据", () => {
    for (const s of ["yellow_card", "red_card", "fouls", "penalty_conceded"]) expect(playerGroupOf(s)).toBe("discipline");
    for (const s of ["goals", "goal_assist", "rating"]) expect(playerGroupOf(s)).toBe("top");
    expect(PLAYER_GROUP_ORDER.slice(0, 4)).toEqual(["top", "attack", "defend", "discipline"]);
  });

  it("PlayerBoards 按分组渲染:吸顶条 + 每组一个带锚点的 section,「其他」只在有内容时出现", () => {
    const board = (stat: string, label: string) => ({
      stat_name: stat,
      label_zh: label,
      direction: "high_good" as const,
      entries: [{ rank: 1, player_id: 1, name: "某人", value: 3, team: { name: "某队" } }],
    });
    const boards = [board("goals", "进球"), board("total_tackle", "抢断"), board("yellow_card", "黄牌"), board("saves", "扑救")];
    const { container } = render(<PlayerBoards boards={boards as unknown as PlayersResponse["boards"]} />);
    const tabs = within(screen.getByTestId("board-section-tabs")).getAllByRole("tab").map((t) => t.textContent);
    expect(tabs).toEqual(["重点数据", "防守", "纪律"]); // 没有进攻榜 → 不出现该分组
    expect(container.querySelector("section#board-top")).not.toBeNull();
    expect(container.querySelector("section#board-other")).toBeNull();
  });
});

describe("BoardSectionTabs 吸顶分组条", () => {
  it("少于 2 个分组不渲染", () => {
    const { container } = render(<BoardSectionTabs sections={[{ id: "top", label: "重点数据" }]} />);
    expect(container.innerHTML).toBe("");
  });

  it("点击某项 → 平滑滚动到对应分组锚点,并高亮该项", () => {
    const scrolled: string[] = [];
    for (const id of ["top", "attack"]) {
      const el = document.createElement("section");
      el.id = boardSectionDomId(id);
      el.scrollIntoView = vi.fn(() => {
        scrolled.push(id);
      }) as unknown as typeof el.scrollIntoView;
      document.body.appendChild(el);
    }
    render(
      <BoardSectionTabs
        sections={[
          { id: "top", label: "重点数据" },
          { id: "attack", label: "进攻" },
        ]}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "进攻" }));
    expect(scrolled).toEqual(["attack"]);
    expect(screen.getByRole("tab", { name: "进攻" }).getAttribute("aria-selected")).toBe("true");
    document.querySelectorAll("section[id^=board-]").forEach((e) => e.remove());
  });
});

describe("LoadMoreList(历史战绩)", () => {
  const items = (n: number) => Array.from({ length: n }, (_, i) => <li key={i}>条目{i + 1}</li>);

  it("默认显示前 10 条,底部有「加载更多」,每次再多 10 条,加载完按钮消失", () => {
    render(
      <ul>
        <LoadMoreList pageSize={10}>{items(25)}</LoadMoreList>
      </ul>,
    );
    expect(screen.getAllByRole("listitem")).toHaveLength(10);
    expect(screen.getByText("条目10")).toBeTruthy();
    expect(screen.queryByText("条目11")).toBeNull();
    const more = screen.getByRole("button", { name: /加载更多/ });
    expect(more.textContent).toContain("还有 15 条");
    fireEvent.click(more);
    expect(screen.getAllByRole("listitem")).toHaveLength(20);
    fireEvent.click(screen.getByRole("button", { name: /加载更多/ }));
    expect(screen.getAllByRole("listitem")).toHaveLength(25);
    expect(screen.queryByRole("button", { name: /加载更多/ })).toBeNull();
  });

  it("不足一页时没有按钮", () => {
    render(
      <ul>
        <LoadMoreList pageSize={10}>{items(7)}</LoadMoreList>
      </ul>,
    );
    expect(screen.getAllByRole("listitem")).toHaveLength(7);
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("MatchRow 只显示开球时间", () => {
  const base = {
    match_id: 1,
    league_id: 47,
    season: "2026/2027",
    date_utc: "2026-09-27",
    status: "NotStarted",
    home: { team_id: 1, name: "主队", crest_url: null },
    away: { team_id: 2, name: "客队", crest_url: null },
    home_score: null,
    away_score: null,
  } as unknown as MatchSummary;

  it("有精确开球时刻:显示北京时间 HH:mm(UTC 10:00 → 18:00),不再重复日期", () => {
    const { container } = render(<MatchRow match={{ ...base, kickoff_at_utc: "2026-09-27T10:00:00Z" }} />);
    expect(container.textContent).toContain("18:00");
    expect(container.textContent).not.toContain("2026-09-27");
  });

  it("只有日期没有精确开球时刻:写「时间待定」,不拿日期顶替", () => {
    const { container } = render(<MatchRow match={{ ...base, kickoff_at_utc: null }} />);
    expect(container.textContent).toContain("时间待定");
    expect(container.textContent).not.toContain("2026-09-27");
  });
});
