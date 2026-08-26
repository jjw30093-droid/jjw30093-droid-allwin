/**
 * 进攻区域三分带(2026-08-25):摘要纯函数、投影组装与渲染分支。
 * 纯内联 SVG 组件(非 ECharts),不适用 §11.3 的 headless 冒烟义务;
 * 文字摘要(§11.2)由 buildAttackingZonesSummary 独立断言。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { AttackingZonesChart } from "@/components/matches/AttackingZonesChart";
import {
  buildAttackingZonesSummary,
  zoneSplitFrom,
} from "@/components/matches/attackingZones";

afterEach(cleanup);

const HOME = { left: 33, center: 29, right: 38 };
const AWAY = { left: 30, center: 34, right: 36 };

describe("buildAttackingZonesSummary(§11.2 文字摘要纯函数)", () => {
  it("两侧齐全:方向与射门图一致(主攻向右/客攻向左),数字原样带 %", () => {
    const t = buildAttackingZonesSummary({
      home: HOME, away: AWAY, homeName: "主", awayName: "客", periodLabel: "全场",
    });
    expect(t).toContain("主(攻向右)左路 33%、中路 29%、右路 38%");
    expect(t).toContain("客(攻向左)左路 30%、中路 34%、右路 36%");
    expect(t).toContain("全场");
  });

  it("单侧缺失:如实说暂无,不编 0", () => {
    const t = buildAttackingZonesSummary({
      home: HOME, away: null, homeName: "主", awayName: "客", periodLabel: "全场",
    });
    expect(t).toContain("客暂无进攻区域数据");
    expect(t).not.toContain("客(攻向左)左路 0%");
  });
});

describe("zoneSplitFrom(投影字段 → 三分区,缺一路整组为 null)", () => {
  it("三路齐全 → 对象", () => {
    expect(zoneSplitFrom(33, 29, 38)).toEqual({ left: 33, center: 29, right: 38 });
  });

  it("任一缺失 → null(不是把缺的那路补 0)", () => {
    expect(zoneSplitFrom(null, 29, 38)).toBeNull();
    expect(zoneSplitFrom(33, undefined, 38)).toBeNull();
    expect(zoneSplitFrom(33, 29, null)).toBeNull();
  });
});

describe("AttackingZonesChart 渲染分支", () => {
  it("两侧全缺且无半场数据 → 整个组件不渲染(诚实空态)", () => {
    const { container } = render(
      <AttackingZonesChart home={null} away={null} homeName="主" awayName="客" />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("有数据:左/中/右徽章(内部枚举不直出)+ 摘要;无半场数据时不渲染切换器", () => {
    render(<AttackingZonesChart home={HOME} away={AWAY} homeName="主" awayName="客" />);
    // 主客各一组徽章 → 左路/中路/右路各出现两次
    expect(screen.getAllByText("左路")).toHaveLength(2);
    expect(screen.getAllByText("中路")).toHaveLength(2);
    expect(screen.getAllByText("右路")).toHaveLength(2);
    expect(screen.getByText("33%")).toBeTruthy();
    // 内部枚举值不得出现在界面上(§11.2)
    expect(screen.queryByText("left")).toBeNull();
    // 图例方向与射门图一致(摘要段落里也有同一措辞,用精确图例文案匹配)
    expect(screen.getByText("主(攻向右 →)")).toBeTruthy();
    expect(screen.getByText("客(← 攻向左)")).toBeTruthy();
    // 没传 byPeriod → 不渲染时段切换器
    expect(screen.queryByRole("tab")).toBeNull();
  });

  it("有半场数据 → 渲染全场/上半场/下半场切换器", () => {
    render(
      <AttackingZonesChart
        home={HOME}
        away={AWAY}
        homeName="主"
        awayName="客"
        byPeriod={{ FirstHalf: { home: { left: 40, center: 25, right: 35 }, away: null } }}
      />,
    );
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["全场", "上半场", "下半场"]);
  });

  it("单侧缺失:只画有数据的一侧徽章,摘要说明另一侧暂无", () => {
    render(<AttackingZonesChart home={HOME} away={null} homeName="主" awayName="客" />);
    expect(screen.getAllByText("左路")).toHaveLength(1);
    expect(screen.getByText(/客暂无进攻区域数据/)).toBeTruthy();
  });
});
