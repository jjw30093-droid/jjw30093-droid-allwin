/**
 * 全站仅有的两种控件(2026-09-26):Tabs(页面级切换)与 Chip(筛选类)。
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Chip } from "@/components/ui/Chip";
import { ChipRow } from "@/components/ui/ChipRow";
import { Tabs } from "@/components/ui/Tabs";
import { LeagueNav } from "@/components/LeagueNav";
import { TableTypeSwitcher } from "@/components/league/TableTypeSwitcher";
import { MatchTabs } from "@/components/matches/MatchTabs";

afterEach(cleanup);

describe("Tabs 链接形态", () => {
  const items = [
    { key: "a", label: "甲", href: "/a" },
    { key: "b", label: "乙", href: "/b" },
  ];

  it("渲染 <nav> + 链接,选中项 aria-current=page,没有 role=tablist", () => {
    render(<Tabs items={items} activeKey="b" ariaLabel="测试导航" />);
    const nav = screen.getByRole("navigation", { name: "测试导航" });
    expect(nav).toBeTruthy();
    expect(screen.queryByRole("tablist")).toBeNull();
    const links = screen.getAllByRole("link");
    expect(links.map((l) => l.getAttribute("href"))).toEqual(["/a", "/b"]);
    expect(links[0].getAttribute("aria-current")).toBeNull();
    expect(links[1].getAttribute("aria-current")).toBe("page");
  });

  it("选中态是 class 区分的(底部粗线样式),不依赖下划线超链接", () => {
    render(<Tabs items={items} activeKey="a" ariaLabel="x" />);
    const [a, b] = screen.getAllByRole("link");
    expect(a.className).toMatch(/tabActive/);
    expect(b.className).not.toMatch(/tabActive/);
  });
});

describe("Tabs 按钮形态", () => {
  const items = [
    { key: "a", label: "甲", id: "t-a", controls: "p-a" },
    { key: "b", label: "乙", id: "t-b", controls: "p-b" },
    { key: "c", label: "丙", id: "t-c", controls: "p-c" },
  ];

  it("role=tablist / tab,aria-selected 与 roving tabindex", () => {
    render(<Tabs items={items} activeKey="b" ariaLabel="切换" onSelect={() => {}} />);
    expect(screen.getByRole("tablist", { name: "切换" })).toBeTruthy();
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.getAttribute("aria-selected"))).toEqual(["false", "true", "false"]);
    expect(tabs.map((t) => t.getAttribute("tabindex"))).toEqual(["-1", "0", "-1"]);
    expect(tabs[0].getAttribute("aria-controls")).toBe("p-a");
    expect(tabs[0].id).toBe("t-a");
  });

  it("点击调用 onSelect;←/→ 循环切换", () => {
    const onSelect = vi.fn();
    render(<Tabs items={items} activeKey="a" ariaLabel="切换" onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("tab", { name: "丙" }));
    expect(onSelect).toHaveBeenLastCalledWith("c");
    const list = screen.getByRole("tablist");
    fireEvent.keyDown(list, { key: "ArrowRight" });
    expect(onSelect).toHaveBeenLastCalledWith("b");
    fireEvent.keyDown(list, { key: "ArrowLeft" }); // 从 a 往左 → 循环到最后一个
    expect(onSelect).toHaveBeenLastCalledWith("c");
  });
});

describe("Chip", () => {
  it("有 href 是链接,选中带 aria-current;没有 href 是按钮,带 aria-pressed", () => {
    render(
      <>
        <Chip href="/x" active>链接</Chip>
        <Chip active={false} onClick={() => {}}>按钮</Chip>
      </>,
    );
    const link = screen.getByRole("link", { name: "链接" });
    expect(link.getAttribute("href")).toBe("/x");
    expect(link.getAttribute("aria-current")).toBe("true");
    const btn = screen.getByRole("button", { name: "按钮" });
    expect(btn.getAttribute("aria-pressed")).toBe("false");
  });

  it("按钮点击触发 onClick", () => {
    const onClick = vi.fn();
    render(<Chip onClick={onClick}>点我</Chip>);
    fireEvent.click(screen.getByRole("button", { name: "点我" }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});

describe("ChipRow 右侧渐隐", () => {
  function mockScrollable(scrollWidth: number, clientWidth: number, scrollLeft = 0) {
    const el = document.querySelector<HTMLElement>('[data-fade-right]')!;
    Object.defineProperty(el, "scrollWidth", { value: scrollWidth, configurable: true });
    Object.defineProperty(el, "clientWidth", { value: clientWidth, configurable: true });
    Object.defineProperty(el, "scrollLeft", { value: scrollLeft, configurable: true, writable: true });
    return el;
  }

  it("放得下时不渐隐;放不下时渐隐;滑到头去掉渐隐", () => {
    render(
      <ChipRow label="联赛">
        <Chip>全部</Chip>
      </ChipRow>,
    );
    const el = mockScrollable(300, 300);
    act(() => {
      fireEvent.scroll(el);
    });
    expect(el.getAttribute("data-fade-right")).toBe("false");

    mockScrollable(600, 300, 0);
    act(() => {
      fireEvent.scroll(el);
    });
    expect(el.getAttribute("data-fade-right")).toBe("true");

    mockScrollable(600, 300, 300); // 滑到最右
    act(() => {
      fireEvent.scroll(el);
    });
    expect(el.getAttribute("data-fade-right")).toBe("false");
  });

  it("label 显示在滚动区外面,分组有无障碍名", () => {
    render(
      <ChipRow label="时间">
        <Chip>今天</Chip>
      </ChipRow>,
    );
    expect(screen.getByRole("group", { name: "时间" })).toBeTruthy();
    expect(screen.getByText("时间")).toBeTruthy();
  });
});

describe("页面级切换全部走同一个 Tabs", () => {
  it("联赛二级导航:5 项、链接形态、选中项 aria-current,赛季带着走", () => {
    render(<LeagueNav leagueId="47" active="standings" season="2025/2026" />);
    const nav = screen.getByRole("navigation", { name: "联赛导航" });
    const links = Array.from(nav.querySelectorAll("a"));
    expect(links.map((a) => a.textContent)).toEqual(["速览", "排名", "赛程", "球队数据", "球员榜"]);
    expect(links.map((a) => a.getAttribute("aria-current"))).toEqual([null, "page", null, null, null]);
    expect(links[1].getAttribute("href")).toBe("/league/47/standings?season=2025%2F2026");
  });

  it("排名页榜别切换:5 项、保留 data-testid,选中项 aria-current", () => {
    render(<TableTypeSwitcher leagueId="47" active="home" season="2024/2025" />);
    const chips = screen.getAllByTestId("table-type-chip");
    expect(chips.map((c) => c.textContent)).toEqual(["总榜", "主场", "客场", "近期", "xG 榜"]);
    expect(chips.map((c) => c.getAttribute("aria-current"))).toEqual([null, "page", null, null, null]);
    expect(chips[1].getAttribute("href")).toMatch(/season=2024/);
  });

  it("比赛详情 MatchTabs:role=tab,点击切换面板显隐,面板 id/aria-controls 对得上", () => {
    render(
      <MatchTabs
        overview={<p>总览内容</p>}
        shots={<p>射门内容</p>}
        stats={<p>统计内容</p>}
        lineup={<p>阵容内容</p>}
        events={<p>事件内容</p>}
        analysis={<p>分析内容</p>}
        odds={<p>赔率内容</p>}
      />,
    );
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["总览", "射门", "统计", "阵容", "事件", "分析", "赔率"]);
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    expect(document.getElementById("match-panel-shots")!.hidden).toBe(true);
    fireEvent.click(screen.getByRole("tab", { name: "射门" }));
    expect(document.getElementById("match-panel-shots")!.hidden).toBe(false);
    expect(document.getElementById("match-panel-overview")!.hidden).toBe(true);
    expect(screen.getByRole("tab", { name: "射门" }).getAttribute("aria-controls")).toBe("match-panel-shots");
  });
});
