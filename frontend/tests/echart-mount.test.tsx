/**
 * EChart(全站唯一图表封装,components/EChart.tsx)挂载时机回归测试。
 *
 * 背景(P3.D):比赛详情页非激活 tab 用 `hidden` 属性保留在 DOM 里
 * (MatchTabs.tsx,SEO 需要——不能改成条件卸载),这些面板里的图表在首次
 * 加载时以 0×0 容器被 React 挂载。旧实现在 mount 的 useEffect 里同步调用
 * `echarts.init(ref.current)`,不管容器当时是不是 0×0,ECharts 内部会对
 * 0 宽/高的容器打印控制台警告。ResizeObserver 之后虽然会在 tab 切换可见时
 * 把图表 resize 正确,但警告本身已经在 init 那一刻打印出来了。
 *
 * 这里 mock `echarts` 模块的 init(不跑真实渲染逻辑),并用一个可手动触发
 * 回调的假 ResizeObserver 模拟"容器何时拥有真实尺寸",直接断言:
 * - 容器尺寸为 0 时,不得调用 echarts.init;
 * - 容器随后变为可见(拿到真实尺寸)后才 init,并且必须用当时最新的 option
 *   (而不是 mount 时刻的旧 option)setOption 一次;
 * - 容器一开始就可见时,init 与首次可见渲染没有额外的用户可感知延迟;
 * - 从未真正 init 过就卸载(tab 从未被打开过)不报错、不误调用 dispose。
 */

import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const initMock = vi.fn();
const setOptionMock = vi.fn();
const resizeMock = vi.fn();
const disposeMock = vi.fn();

vi.mock("echarts", () => ({
  init: (...args: unknown[]) => {
    initMock(...args);
    return { setOption: setOptionMock, resize: resizeMock, dispose: disposeMock };
  },
}));

// jsdom 没有 ResizeObserver,也没有真实布局引擎(clientWidth/clientHeight 恒为
// 0),所以这里不能靠"读 DOM 尺寸"来模拟 hidden vs 可见,而是自己实现一个
// 可以手动触发回调、并且完全掌控 contentRect 的假实现——这正好对应
// EChart.tsx 要依赖的信号来源(ResizeObserver 回调里的 contentRect)。
type RoEntry = { contentRect: { width: number; height: number } };
type RoCallback = (entries: RoEntry[]) => void;

class FakeResizeObserver {
  callback: RoCallback;
  observedEls: Element[] = [];
  disconnected = false;
  constructor(cb: RoCallback) {
    this.callback = cb;
    roInstances.push(this);
  }
  observe(el: Element) {
    this.observedEls.push(el);
  }
  unobserve() {}
  disconnect() {
    this.disconnected = true;
  }
}

let roInstances: FakeResizeObserver[];

function fireResize(index: number, rect: { width: number; height: number }) {
  const inst = roInstances[index];
  inst.callback([{ contentRect: rect }]);
}

beforeEach(() => {
  roInstances = [];
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  initMock.mockClear();
  setOptionMock.mockClear();
  resizeMock.mockClear();
  disposeMock.mockClear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const { EChart } = await import("@/components/EChart");

describe("EChart 挂载时机(隐藏 tab 场景,ECharts 0×0 告警回归护栏)", () => {
  it("容器尺寸为 0(ResizeObserver 首次回调 width/height 均为 0)时,不触发 echarts.init", () => {
    render(<EChart option={{}} ariaSummary="测试摘要" />);
    expect(roInstances.length).toBe(1);

    // 模拟 MatchTabs 非激活面板:hidden 属性下容器是 0×0。
    fireResize(0, { width: 0, height: 0 });

    expect(initMock).not.toHaveBeenCalled();
  });

  it("容器随后变为可见(拿到真实尺寸)后才 init,并使用当时最新的 option", () => {
    const { rerender } = render(
      <EChart option={{ title: { text: "v1" } }} ariaSummary="测试摘要" />,
    );
    fireResize(0, { width: 0, height: 0 });
    expect(initMock).not.toHaveBeenCalled();

    // tab 还没打开时,option 已经因为数据到达而更新过一次。
    rerender(<EChart option={{ title: { text: "v2" } }} ariaSummary="测试摘要" />);
    expect(setOptionMock).not.toHaveBeenCalled(); // 还没 init,不能调用 setOption

    // 用户切换到这个 tab,容器变为可见。
    fireResize(0, { width: 320, height: 280 });

    expect(initMock).toHaveBeenCalledTimes(1);
    expect(setOptionMock).toHaveBeenCalledTimes(1);
    expect(setOptionMock.mock.calls[0][0]).toMatchObject({ title: { text: "v2" } });

    // init 之后的后续 resize 回调必须走 chart.resize(),不能重复 init。
    fireResize(0, { width: 360, height: 280 });
    expect(initMock).toHaveBeenCalledTimes(1);
    expect(resizeMock).toHaveBeenCalledTimes(1);
  });

  it("容器一开始就可见:init 与首次可见渲染没有额外延迟(正常路径,不能因这次改动回归)", () => {
    render(<EChart option={{ title: { text: "v1" } }} ariaSummary="测试摘要" />);
    // 真实浏览器里,可见容器的 ResizeObserver 首次回调几乎在 observe() 之后
    // 同一帧内触发——这里直接同步触发来模拟这一行为,断言不会因为改了初始化
    // 时机而"额外"推迟到某个后续事件。
    fireResize(0, { width: 300, height: 280 });

    expect(initMock).toHaveBeenCalledTimes(1);
    expect(setOptionMock).toHaveBeenCalledTimes(1);
    expect(setOptionMock.mock.calls[0][0]).toMatchObject({ title: { text: "v1" } });
  });

  it("从未真正 init 过就被卸载(切换太快,这个 tab 从没打开过):不报错,不误调用 dispose", () => {
    const { unmount } = render(<EChart option={{}} ariaSummary="测试摘要" />);
    fireResize(0, { width: 0, height: 0 });
    expect(initMock).not.toHaveBeenCalled();

    expect(() => unmount()).not.toThrow();
    expect(disposeMock).not.toHaveBeenCalled();
    expect(roInstances[0].disconnected).toBe(true);
  });

  it("正常卸载(已经 init 过):disconnect 且 dispose 真正的 chart 实例", () => {
    const { unmount } = render(<EChart option={{}} ariaSummary="测试摘要" />);
    fireResize(0, { width: 300, height: 280 });
    expect(initMock).toHaveBeenCalledTimes(1);

    unmount();
    expect(disposeMock).toHaveBeenCalledTimes(1);
    expect(roInstances[0].disconnected).toBe(true);
  });
});
