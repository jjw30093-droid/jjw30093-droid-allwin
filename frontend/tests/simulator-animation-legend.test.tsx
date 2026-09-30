/** 动画图例:只有参数里带真实射门样本库时才说"取自真实射门",否则如实写"示意"。 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MatchAnimation } from "@/app/simulator/MatchAnimation";

afterEach(cleanup);

// jsdom 没有 canvas 2D 上下文与 ResizeObserver:动画组件拿不到上下文时不画,文字部分照常渲染
vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });

const single = { seed: 1, score: [0, 0] as [number, number], events: [], totalTicks: 96, halfTimeTick: 47, stoppage: [2, 4] as [number, number], epsilon: [0, 0] as [number, number] };

describe("动画图例", () => {
  it("有样本库:取自真实射门", () => {
    render(<MatchAnimation single={single} names={["甲", "乙"]} onDone={() => {}} realShots />);
    expect(screen.getByText(/取自五大联赛真实射门/)).toBeTruthy();
  });
  it("没有样本库:示意", () => {
    render(<MatchAnimation single={single} names={["甲", "乙"]} onDone={() => {}} />);
    expect(screen.queryByText(/真实射门/)).toBeNull();
    expect(screen.getByText(/射门位置为示意/)).toBeTruthy();
  });
});
