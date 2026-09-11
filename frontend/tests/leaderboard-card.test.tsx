/**
 * LeaderboardCard 榜单卡(2026-09-11 二次修正,对照 FotMob 联赛球队/球员数据
 * tab 的真实排版)。
 *
 * 第一版做成了横向滑动小卡,与 FotMob 实际排版不符(它是纵向 top-3 列表 +
 * 右上角箭头展开),站长指出后改回纵向。这批断言锁住的就是那几条:
 * 默认只露 3 行、其余折在原生 <details> 里、每行是"排名+队徽/头像+名字+数值"
 * 一行到底、第 1 名数值带胶囊。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { LeaderboardCard, type LeaderboardRow } from "@/components/LeaderboardCard";

afterEach(cleanup);

function rows(n: number, overrides: Partial<LeaderboardRow> = {}): LeaderboardRow[] {
  return Array.from({ length: n }, (_, i) => ({
    rank: i + 1,
    name: `队伍${i + 1}`,
    value: `${10 - i}.0`,
    ...overrides,
  }));
}

describe("LeaderboardCard 头像/队徽", () => {
  it("avatar.kind='player' 渲染 PlayerAvatar", () => {
    render(
      <LeaderboardCard
        title="榜单"
        rows={rows(1, { avatar: { kind: "player", playerId: 1077894 } })}
      />,
    );
    expect(screen.getByTestId("player-avatar-image")).not.toBeNull();
  });

  it("avatar.kind='team' 渲染 TeamBadge", () => {
    render(
      <LeaderboardCard
        title="榜单"
        rows={rows(1, { avatar: { kind: "team", crestUrl: "/crest.png" } })}
      />,
    );
    expect(screen.getByTestId("team-badge-image")).not.toBeNull();
  });

  it("crestUrl 缺失时退化为队名缩写,不是空白/裂图", () => {
    render(
      <LeaderboardCard
        title="榜单"
        rows={rows(1, { avatar: { kind: "team", crestUrl: null } })}
      />,
    );
    expect(screen.getByTestId("team-badge-fallback")).not.toBeNull();
  });

  it("不传 avatar 时不渲染任何头像槽(纯文字兼容路径)", () => {
    render(<LeaderboardCard title="榜单" rows={rows(1)} />);
    expect(screen.queryByTestId("player-avatar-image")).toBeNull();
    expect(screen.queryByTestId("team-badge-image")).toBeNull();
    expect(screen.queryByTestId("team-badge-fallback")).toBeNull();
  });
});

describe("LeaderboardCard 纵向 top-3 + 折叠", () => {
  it("默认只露 3 行,其余折进 details(不是横滑)", () => {
    const { container } = render(<LeaderboardCard title="榜单" rows={rows(10)} />);
    const details = container.querySelector("details");
    expect(details).not.toBeNull();
    // 折叠态:details 未展开
    expect(details!.hasAttribute("open")).toBe(false);
    // top-3 在 summary 里(始终可见),其余在 details 正文里
    const summaryRows = details!.querySelector("summary")!.querySelectorAll("li");
    expect(summaryRows.length).toBe(3);
    // 全部 10 行都在 DOM 里(折叠只是视觉隐藏,不丢数据)
    expect(container.querySelectorAll("li").length).toBe(10);
  });

  it("行是纵向列表,不是横滑轨道", () => {
    const { container } = render(<LeaderboardCard title="榜单" rows={rows(10)} />);
    expect(container.querySelectorAll("ol").length).toBeGreaterThan(0);
    // 第一版的横滑容器已经不存在
    expect(container.querySelector('[role="list"]')).toBeNull();
  });

  it("行数不超过 3 时不套 details,免得出现点不动的箭头", () => {
    const { container } = render(<LeaderboardCard title="榜单" rows={rows(3)} />);
    expect(container.querySelector("details")).toBeNull();
    expect(container.querySelectorAll("li").length).toBe(3);
  });

  it("折叠部分的序号接着排,不从 1 重来", () => {
    const { container } = render(<LeaderboardCard title="榜单" rows={rows(10)} />);
    const lists = container.querySelectorAll("ol");
    expect(lists[lists.length - 1].getAttribute("start")).toBe("4");
  });

  it("summary 有简短的无障碍名,不会把三行内容连起来念", () => {
    render(<LeaderboardCard title="场均射门榜" rows={rows(10)} />);
    const summary = screen.getByLabelText("场均射门榜,展开全部 10 名");
    expect(summary).not.toBeNull();
  });
});

describe("LeaderboardCard 数值与空态", () => {
  it("第 1 名数值带胶囊,第 2 名不带", () => {
    render(<LeaderboardCard title="榜单" rows={rows(10)} />);
    expect(screen.getByText("10.0").className).toMatch(/valueTop/);
    expect(screen.getByText("9.0").className).not.toMatch(/valueTop/);
  });

  it("空数组渲染暂无数据,不渲染任何列表", () => {
    const { container } = render(<LeaderboardCard title="榜单" rows={[]} />);
    expect(screen.getByText("暂无数据")).not.toBeNull();
    expect(container.querySelector("ol")).toBeNull();
    expect(container.querySelector("details")).toBeNull();
  });

  it("榜首有安全队色时下发四个主题变量,其它名次不下发", () => {
    const { container } = render(
      <LeaderboardCard
        title="控球率榜"
        rows={[
          { rank: 1, name: "切尔西", value: "60.1%", teamColor: { light: "#051495", dark: "#033cc1" } },
          { rank: 2, name: "阿森纳", value: "58.0%", teamColor: { light: "#051495", dark: "#033cc1" } },
        ]}
      />
    );
    const spans = Array.from(container.querySelectorAll<HTMLElement>("li span"));
    const first = spans.find((el) => el.textContent === "60.1%")!;
    const second = spans.find((el) => el.textContent === "58.0%")!;
    expect(first.style.getPropertyValue("--pill-bg")).toBe("#051495");
    expect(first.style.getPropertyValue("--pill-ink")).toBe("#ffffff");
    expect(first.style.getPropertyValue("--pill-bg-dark")).toBe("#033cc1");
    expect(first.style.getPropertyValue("--pill-ink-dark")).toBe("#ffffff");
    // 第 2 名根本不是胶囊,不该带任何队色变量
    expect(second.style.getPropertyValue("--pill-bg")).toBe("");
  });

  it("没有队色/队色不安全时不下发变量,CSS 回退品牌色", () => {
    const { container } = render(
      <LeaderboardCard
        title="控球率榜"
        rows={[
          { rank: 1, name: "无色队", value: "60.1%" },
          { rank: 2, name: "陪跑", value: "58.0%" },
        ]}
      />
    );
    const first = Array.from(container.querySelectorAll<HTMLElement>("li span")).find(
      (el) => el.textContent === "60.1%"
    )!;
    expect(first.getAttribute("style")).toBeNull();
  });
});
