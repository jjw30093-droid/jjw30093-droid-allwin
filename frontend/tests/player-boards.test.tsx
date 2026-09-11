/**
 * PlayerBoards 头像改造(2026-09-11)——只补充"球员头像随行下发"这一点,
 * 数值格式化逻辑(整数/两位小数)不是本次改动范围,不重复断言。
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { PlayerBoards } from "@/components/league/PlayerBoards";
import type { PlayersResponse } from "@/lib/api-v1";

afterEach(cleanup);

function boards(overrides: Partial<PlayersResponse["boards"][number]> = {}) {
  return [
    {
      stat_name: "goals",
      label_zh: "进球",
      entries: [
        {
          player_id: "1077894",
          name: "测试球员",
          name_en: "Test Player",
          team: { team_id: 1001, name: "阿森纳", name_en: "Arsenal", crest_url: null },
          rank: 1,
          value: 5,
        },
      ],
      ...overrides,
    },
  ] as PlayersResponse["boards"];
}

describe("PlayerBoards 头像", () => {
  it("渲染出球员头像(player_id 已随行下发)", () => {
    render(<PlayerBoards boards={boards()} />);
    expect(screen.getByTestId("player-avatar-image")).not.toBeNull();
    const img = screen.getByTestId("player-avatar-image").querySelector("img");
    expect(img?.getAttribute("src")).toContain("/playerimages/1077894.png");
  });

  it("取图失败时退化为姓名首字,不是裂图", () => {
    render(<PlayerBoards boards={boards()} />);
    const img = screen.getByTestId("player-avatar-image").querySelector("img")!;
    fireEvent.error(img);
    expect(screen.queryByTestId("player-avatar-image")).toBeNull();
    expect(screen.getByTestId("player-avatar-fallback").textContent).toBe("测");
  });

  it("姓名与队名(subtitle)照常渲染", () => {
    render(<PlayerBoards boards={boards()} />);
    expect(screen.getByText("测试球员")).not.toBeNull();
    expect(screen.getByText(/阿森纳/)).not.toBeNull();
  });
});
