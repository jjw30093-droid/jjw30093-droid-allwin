/** 战术侧重卡片:互斥置灰、最多 2 个、胜率变化展示、计算中、休息不足开关。只测交互与文字,不测样式。 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TeamFocusCard } from "@/app/simulator/TeamFocusCard";
import type { Focus, TeamSetup } from "@/features/simulator/engine";
import type { SideImpact } from "@/features/simulator/focusImpact";
import { matchSetup, simParams, teamSetup } from "./fixtures/simulatorParams";

afterEach(cleanup);

function renderCard(focuses: Focus[], impact: SideImpact | null, onChange = vi.fn(), shortRest = false) {
  const setup: TeamSetup = { ...teamSetup(10, "h"), focuses, shortRest };
  render(
    <TeamFocusCard params={simParams()} side={0} setup={setup} opponentName="曼城" impactSetup={matchSetup()} impact={impact} preparedOk onChange={onChange} />,
  );
  return onChange;
}

const IMPACT: SideImpact = { base: 0.443, selected: null, singlePp: { setpiece: 1.1, counter: 0.6, possession: -0.3, press: 3.4, crossing: 0, lowblock: -1.2 } };

describe("战术侧重卡片", () => {
  it("每个方块有说明与胜率变化;提升够多的标适合本场;对手写明暂定", () => {
    renderCard([], IMPACT);
    expect(screen.getByTestId("focus-setpiece").textContent).toContain("角球、任意球机会更多");
    expect(screen.getByTestId("focus-setpiece").textContent).toContain("+1.1");
    expect(screen.getByTestId("focus-possession").textContent).toContain("−0.3");
    expect(screen.getByTestId("focus-crossing").textContent).toContain("±0.0");
    expect(screen.getByTestId("focus-press").textContent).toContain("+3.4 · 适合本场"); // 阈值 FIT_THRESHOLD_PP = 3
    expect(screen.getByTestId("focus-setpiece").textContent).not.toContain("适合本场");
    expect(screen.getByText(/对阵曼城\(暂定\)/)).toBeTruthy();
    expect(screen.getByTestId("focus-winrate").textContent).toContain("44.3%");
  });

  it("选了高位逼抢:稳守反击置灰并写明互斥,点了不改;胜率显示变化", () => {
    const onChange = renderCard(["press"], { ...IMPACT, selected: 0.467 });
    const low = screen.getByTestId("focus-lowblock");
    expect(low.getAttribute("aria-disabled")).toBe("true");
    expect(low.textContent).toContain("与逼抢互斥");
    fireEvent.click(low);
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByTestId("focus-possession").getAttribute("aria-disabled")).toBeNull();
    expect(screen.getByTestId("focus-winrate").textContent).toMatch(/44\.3%.*46\.7%.*\+2\.4/);
  });

  it("已选满 2 个:其余置灰写明最多 2 个;已选的仍可取消", () => {
    const onChange = renderCard(["setpiece", "press"], IMPACT);
    expect(screen.getByTestId("focus-crossing").textContent).toContain("最多 2 个");
    fireEvent.click(screen.getByTestId("focus-crossing"));
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("focus-setpiece"));
    expect(onChange.mock.calls[0][0].focuses).toEqual(["press"]);
  });

  it("胜率还在计算:方块显示 …,顶部显示计算中", () => {
    renderCard([], null);
    expect(screen.getByTestId("focus-winrate").textContent).toContain("计算中");
    expect(screen.getByTestId("focus-counter").textContent).toContain("…");
  });

  it("休息不足是单独的开关", () => {
    const onChange = renderCard([], IMPACT);
    const sw = screen.getByRole("switch", { name: "休息不足 3 天" });
    expect(sw.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(sw);
    expect(onChange.mock.calls[0][0].shortRest).toBe(true);
  });
});
