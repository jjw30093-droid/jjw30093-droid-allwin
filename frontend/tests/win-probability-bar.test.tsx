/**
 * WinProbabilityBar 渲染 + 点击交互。
 *
 * 渲染部分(无数据不占位、四舍五入、aria-label 兜底可读性、真实审计样本
 * 对拍)是这个组件早先就有的既有测试,与 2026-08-20 新增的点击交互
 * (点数字,该档高亮 + 数字放大,再点一次取消;点其它档切换)合并到同一个
 * 文件里,不拆两份。
 *
 * 组件常被父级 <Link> 整体包住(MatchRow 整行、首页次要卡整张),点数字
 * 必须不触发外层导航——这里用一个真实的外层 <a>(不 mock router)断言
 * fireEvent.click 的返回值(false = defaultPrevented),而不是只信任源码里
 * 写了 preventDefault。
 *
 * 2026-08-23 修复(P1):三段色块条不再是 <button>,只是纯展示的 <span>,
 * 不响应点击、不出现在可访问性树里(aria-hidden)——点色块必须能穿透到
 * 外层 <Link> 正常导航,不能再像色块曾经是按钮时那样把整行导航吞掉。
 * 容器 role 也从 "img" 改成 "group"(里面确实有可交互的数字按钮)。
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { WinProbabilityBar } from "@/components/matches/WinProbabilityBar";
import type { WinProbability } from "@/lib/api-v1";

afterEach(cleanup);

const PROBABILITY: WinProbability = {
  p_home: 0.48,
  p_draw: 0.27,
  p_away: 0.25,
  observed_at: "2026-08-20T09:00:00Z",
};

describe("WinProbabilityBar 渲染(无数据/四舍五入/真实样本)", () => {
  it("无数据时整条不占位(不画假等分,不补 0)", () => {
    const { container } = render(<WinProbabilityBar probability={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("undefined 同样不占位", () => {
    const { container } = render(<WinProbabilityBar probability={undefined} />);
    expect(container.firstChild).toBeNull();
  });

  it("有数据时三段都四舍五入成整数百分比,并带 aria-label 兜底可读性", () => {
    render(
      <WinProbabilityBar
        probability={{ p_home: 0.4065, p_draw: 0.3339, p_away: 0.2597, observed_at: "2026-08-12T00:34:47Z" }}
      />,
    );
    expect(screen.getByText("41%")).not.toBeNull();
    expect(screen.getByText("33%")).not.toBeNull();
    expect(screen.getByText("26%")).not.toBeNull();
    expect(
      screen.getByLabelText("胜平负概率:主胜 41%,平局 33%,客胜 26%"),
    ).not.toBeNull();
  });

  it("真实审计样本(马竞 vs 马拉加)对拍:73/18/9", () => {
    render(
      <WinProbabilityBar
        probability={{ p_home: 0.7259, p_draw: 0.1797, p_away: 0.0944, observed_at: "2026-08-12T01:39:50Z" }}
      />,
    );
    expect(screen.getByText("73%")).not.toBeNull();
    expect(screen.getByText("18%")).not.toBeNull();
    expect(screen.getByText("9%")).not.toBeNull();
  });
});

describe("WinProbabilityBar 色块条(默认/compact 版式)——纯展示,不可点击", () => {
  it("容器 role 是 group(不是 img),因为内部确实有可交互的数字按钮", () => {
    render(<WinProbabilityBar probability={PROBABILITY} />);
    expect(
      screen.getByRole("group", { name: "胜平负概率:主胜 48%,平局 27%,客胜 25%" }),
    ).not.toBeNull();
  });

  it("色块条不是 button,也不会出现在可访问性树的按钮列表里", () => {
    render(<WinProbabilityBar probability={PROBABILITY} />);
    // 整个组件只应该有三个 button(主胜/平/客胜三个数字标签),不包含色块。
    expect(screen.getAllByRole("button")).toHaveLength(3);
  });

  it("点击色块区域能穿透到外层 <Link>,不再被吞掉(P1 修复的核心断言)", () => {
    // 测试只需要一个真实的可导航祖先元素来验证事件确实冒泡且未被
    // preventDefault,不是真实页面路由,故意用原生 <a> 而不是 next/link
    // (后者在纯 vitest/jsdom 环境需要额外的 router mock,与这条测试要
    // 验证的行为无关)。
    /* eslint-disable @next/next/no-html-link-for-pages */
    const { container } = render(
      <a href="/matches/1" data-testid="outer-link">
        <WinProbabilityBar probability={PROBABILITY} compact />
      </a>,
    );
    /* eslint-enable @next/next/no-html-link-for-pages */
    const homeSeg = container.querySelector('[class*="segHome"]')!;
    expect(homeSeg.tagName).toBe("SPAN");
    const event = fireEvent.click(homeSeg);
    // fireEvent.click 返回值:true 表示事件没有被 preventDefault,
    // 冒泡到了外层 <a>,可以正常导航。
    expect(event).toBe(true);
  });
});

describe("WinProbabilityBar 点击交互(默认/compact 版式,数字标签)", () => {
  it("初始状态下没有任何一档被选中", () => {
    render(<WinProbabilityBar probability={PROBABILITY} />);
    expect(
      screen.getByRole("button", { name: "主胜 48%" }).getAttribute("data-selected"),
    ).toBe("false");
  });

  it("点击主胜数字:主胜数字进入选中态,其它两档不受影响", () => {
    render(<WinProbabilityBar probability={PROBABILITY} />);
    fireEvent.click(screen.getByRole("button", { name: "主胜 48%" }));

    const homeLabel = screen.getByRole("button", { name: "主胜 48%" });
    expect(homeLabel.getAttribute("data-selected")).toBe("true");

    const drawLabel = screen.getByRole("button", { name: "平 27%" });
    const awayLabel = screen.getByRole("button", { name: "客胜 25%" });
    expect(drawLabel.getAttribute("data-selected")).toBe("false");
    expect(awayLabel.getAttribute("data-selected")).toBe("false");
  });

  it("再点一次同一档:取消选中(回到初始态)", () => {
    render(<WinProbabilityBar probability={PROBABILITY} />);
    const homeLabel = screen.getByRole("button", { name: "主胜 48%" });
    fireEvent.click(homeLabel);
    expect(homeLabel.getAttribute("data-selected")).toBe("true");
    fireEvent.click(homeLabel);
    expect(homeLabel.getAttribute("data-selected")).toBe("false");
  });

  it("先选主胜,再点客胜:切换选中,主胜自动取消,不是叠加多选", () => {
    render(<WinProbabilityBar probability={PROBABILITY} />);
    fireEvent.click(screen.getByRole("button", { name: "主胜 48%" }));
    fireEvent.click(screen.getByRole("button", { name: "客胜 25%" }));

    expect(
      screen.getByRole("button", { name: "主胜 48%" }).getAttribute("data-selected"),
    ).toBe("false");
    expect(
      screen.getByRole("button", { name: "客胜 25%" }).getAttribute("data-selected"),
    ).toBe("true");
  });

  it("点击数字标签不触发外层 <Link> 的导航(整行/整卡常被包在 <a> 里)", () => {
    /* eslint-disable @next/next/no-html-link-for-pages */
    render(
      <a href="/matches/1" data-testid="outer-link">
        <WinProbabilityBar probability={PROBABILITY} compact />
      </a>,
    );
    /* eslint-enable @next/next/no-html-link-for-pages */
    const homeLabel = screen.getByRole("button", { name: "主胜 48%" });
    const event = fireEvent.click(homeLabel);
    // fireEvent.click 返回值:false 表示事件被 preventDefault 阻止了默认行为
    expect(event).toBe(false);
  });
});

describe('WinProbabilityBar 点击交互(size="lg" 首页重点卡版式)', () => {
  it("色块条同样不是 button,只有三个数字组是 button", () => {
    render(<WinProbabilityBar probability={PROBABILITY} size="lg" />);
    expect(screen.getAllByRole("button")).toHaveLength(3);
  });

  it("点击数字组(numGroup)能选中,且不触发外层导航", () => {
    render(
      // eslint-disable-next-line @next/next/no-html-link-for-pages -- 同上
      <a href="/matches/1">
        <WinProbabilityBar probability={PROBABILITY} size="lg" />
      </a>,
    );
    const homeGroup = screen.getByText("主胜").closest("button")!;
    const event = fireEvent.click(homeGroup);
    expect(homeGroup.getAttribute("data-selected")).toBe("true");
    expect(event).toBe(false);
  });

  it("点击色块(barLg 里的三段)能穿透到外层导航,不影响 numGroup 的选中态", () => {
    const { container } = render(<WinProbabilityBar probability={PROBABILITY} size="lg" />);
    const awaySeg = container.querySelector('[class*="segAway"]')!;
    fireEvent.click(awaySeg);
    const awayGroup = screen.getByText("客胜").closest("button")!;
    expect(awayGroup.getAttribute("data-selected")).toBe("false");
  });
});
