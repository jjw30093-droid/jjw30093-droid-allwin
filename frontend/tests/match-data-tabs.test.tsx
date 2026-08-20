/**
 * MatchDataTabs:数据 tab 内的四个子 tab(阵容/风格/球员/射门)。
 *
 * 2026-08-20 新增「射门」子 tab(两队近 5 场射门分布,此前后端/组件都已
 * 写好但没有任何页面接上——见 MatchDetailBody.tsx::DataGroup 的注释),这里
 * 之前完全没有测试覆盖,一并补上基础的切换/可见性/键盘导航行为。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MatchDataTabs } from "@/components/matches/MatchDataTabs";

afterEach(cleanup);

function panelHiddenFor(testId: string): boolean {
  const panel = screen.getByTestId(testId).closest<HTMLElement>('[role="tabpanel"]');
  if (!panel) throw new Error(`no ancestor tabpanel for testid ${testId}`);
  return panel.hidden;
}

function renderTabs() {
  return render(
    <MatchDataTabs
      lineup={<div data-testid="panel-lineup">阵容内容</div>}
      style={<div data-testid="panel-style">风格内容</div>}
      players={<div data-testid="panel-players">球员内容</div>}
      shots={<div data-testid="panel-shots">射门内容</div>}
    />,
  );
}

describe("MatchDataTabs", () => {
  it("四个子 tab 全部渲染,默认展示阵容", () => {
    renderTabs();
    expect(screen.getByRole("tab", { name: "阵容" })).not.toBeNull();
    expect(screen.getByRole("tab", { name: "风格" })).not.toBeNull();
    expect(screen.getByRole("tab", { name: "球员" })).not.toBeNull();
    expect(screen.getByRole("tab", { name: "射门" })).not.toBeNull();
    expect(panelHiddenFor("panel-lineup")).toBe(false);
    expect(panelHiddenFor("panel-shots")).toBe(true);
  });

  it("点击「射门」切换到射门面板,其余面板隐藏(仍在 DOM 里,不是被卸载)", () => {
    renderTabs();
    fireEvent.click(screen.getByRole("tab", { name: "射门" }));
    expect(panelHiddenFor("panel-shots")).toBe(false);
    expect(panelHiddenFor("panel-lineup")).toBe(true);
    // hidden 面板的内容仍在 DOM 里(role=tabpanel 用 hidden 属性,不是条件渲染)
    expect(screen.getByText("阵容内容")).not.toBeNull();
  });

  it("方向键从最后一个 tab(射门)按右箭头会回绕到第一个 tab(阵容)", () => {
    renderTabs();
    const shotsTab = screen.getByRole("tab", { name: "射门" });
    fireEvent.click(shotsTab);
    fireEvent.keyDown(shotsTab, { key: "ArrowRight" });
    expect(panelHiddenFor("panel-lineup")).toBe(false);
    expect(panelHiddenFor("panel-shots")).toBe(true);
  });
});
