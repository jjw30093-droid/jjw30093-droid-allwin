/**
 * GoalkeeperSection:xGOT 数据不完整(某场缺 expected_goals_on_target_faced)
 * 时,不得现算"阻止进球"估算——那会用不完整分母算出一个看似精确、实则
 * 被低估的数字,把缺数据的门将显示成表现更差。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { GoalkeeperSection } from "@/components/matches/MatchDataModules";
import type { components } from "@/lib/api-types";

afterEach(cleanup);

type KeeperRow = components["schemas"]["MatchPreviewKeeperDTO"];

function keeper(over: Partial<KeeperRow> = {}): KeeperRow {
  return {
    player_id: "p1",
    name: "测试门将",
    appearances: 5,
    saves: 10,
    xgot_faced: 6.0,
    xgot_faced_complete: true,
    goals_conceded: 4,
    goals_prevented: null,
    ...over,
  };
}

describe("GoalkeeperSection 阻止进球估算的诚实边界", () => {
  it("goals_prevented 直接来源存在时,展示来源值,标注「阻止进球」(非估算)", () => {
    render(<GoalkeeperSection teams={[{ teamName: "测试队", keepers: [keeper({ goals_prevented: 1.5 })] }]} />);
    expect(screen.getByText("+1.50")).not.toBeNull();
    expect(screen.getByText("阻止进球")).not.toBeNull();
  });

  it("goals_prevented 缺失但 xGOT 完整时,现算估算值并标注「估算」", () => {
    render(
      <GoalkeeperSection
        teams={[{ teamName: "测试队", keepers: [keeper({ goals_prevented: null, xgot_faced: 6.0, xgot_faced_complete: true, goals_conceded: 4 })] }]}
      />,
    );
    expect(screen.getByText("+2.00")).not.toBeNull();
    expect(screen.getByText("阻止进球 · 估算")).not.toBeNull();
  });

  it("goals_prevented 缺失且 xGOT 不完整时,不得现算估算数字,必须显式说明数据不足", () => {
    const { container } = render(
      <GoalkeeperSection
        teams={[{
          teamName: "测试队",
          keepers: [keeper({ goals_prevented: null, xgot_faced: 3.0, xgot_faced_complete: false, goals_conceded: 4 })],
        }]}
      />,
    );
    // 不完整分母算出的估算数字(3.0-4=-1.00)不能出现
    expect(screen.queryByText("−1.00")).toBeNull();
    expect(screen.queryByText("+-1.00")).toBeNull();
    expect(container.textContent).toMatch(/数据不足|无法估算/);
  });

  it("goals_prevented 缺失且 xGOT 完全没有数据时,不得现算,也不得把 xGOT 显示成 0", () => {
    const { container } = render(
      <GoalkeeperSection
        teams={[{
          teamName: "测试队",
          keepers: [keeper({ goals_prevented: null, xgot_faced: null, xgot_faced_complete: false, goals_conceded: 4 })],
        }]}
      />,
    );
    expect(container.textContent).toMatch(/数据不足|无法估算/);
    // "面对 xGOT" 那一格不能显示 0.00(0 是"确实面对了 0 次射正"的合法值)
    const cells = container.querySelectorAll('[class*="keeperCell"]');
    const xgotCell = Array.from(cells).find((c) => c.textContent?.includes("面对 xGOT"));
    expect(xgotCell?.textContent).not.toMatch(/0\.00/);
  });
});
