/**
 * LeaderboardCard 横向卡片轮播改造(2026-09-11,对照 FotMob 官方安卓包联赛
 * Stats tab)。核心是新增的判别式联合 `avatar` 字段——球员走 PlayerAvatar
 * (热链 FotMob CDN),球队走 TeamBadge(同源代理),两者互斥,不传时保留纯
 * 文字兼容路径(不强制所有调用方都有头像来源)。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { LeaderboardCard, type LeaderboardRow } from "@/components/LeaderboardCard";

afterEach(cleanup);

function rows(overrides: Partial<LeaderboardRow>[]): LeaderboardRow[] {
  return overrides.map((o, i) => ({
    rank: i + 1,
    name: `实体${i + 1}`,
    value: "1.0",
    ...o,
  }));
}

describe("LeaderboardCard 头像/队徽", () => {
  it("avatar.kind='player' 渲染 PlayerAvatar", () => {
    render(
      <LeaderboardCard
        title="榜单"
        rows={rows([{ avatar: { kind: "player", playerId: 1077894 } }])}
      />,
    );
    expect(screen.getByTestId("player-avatar-image")).not.toBeNull();
  });

  it("avatar.kind='team' 渲染 TeamBadge", () => {
    render(
      <LeaderboardCard
        title="榜单"
        rows={rows([{ avatar: { kind: "team", crestUrl: "/crest.png" } }])}
      />,
    );
    expect(screen.getByTestId("team-badge-image")).not.toBeNull();
  });

  it("球队榜 crestUrl 缺失时退化为队徽兜底缩写,不是空白/裂图", () => {
    render(
      <LeaderboardCard
        title="榜单"
        rows={rows([{ name: "布莱顿", avatar: { kind: "team", crestUrl: null } }])}
      />,
    );
    expect(screen.getByTestId("team-badge-fallback")).not.toBeNull();
  });

  it("不传 avatar 时不渲染任何头像槽(纯文字兼容路径)", () => {
    render(<LeaderboardCard title="榜单" rows={rows([{}])} />);
    expect(screen.queryByTestId("player-avatar-image")).toBeNull();
    expect(screen.queryByTestId("player-avatar-fallback")).toBeNull();
    expect(screen.queryByTestId("team-badge-image")).toBeNull();
    expect(screen.queryByTestId("team-badge-fallback")).toBeNull();
  });
});

describe("LeaderboardCard 排名与空态", () => {
  it("第 1 名应用高亮样式,第 4 名不应用", () => {
    render(<LeaderboardCard title="榜单" rows={rows([{}, {}, {}, {}])} />);
    // 排名文字本身是唯一的("1"/"2"/"3"/"4",不会与 value "1.0" 等其它文本撞车)
    expect(screen.getByText("1").className).toMatch(/rankTop3/);
    expect(screen.getByText("4").className).not.toMatch(/rankTop3/);
  });

  it("空数组渲染暂无数据,不渲染横滑轨道", () => {
    const { container } = render(<LeaderboardCard title="榜单" rows={[]} />);
    expect(screen.getByText("暂无数据")).not.toBeNull();
    expect(container.querySelector('[role="list"]')).toBeNull();
  });

  it("横滑轨道容器存在,每行是 listitem", () => {
    const { container } = render(
      <LeaderboardCard title="榜单" rows={rows([{}, {}])} />,
    );
    expect(container.querySelector('[role="list"]')).not.toBeNull();
    expect(container.querySelectorAll('[role="listitem"]').length).toBe(2);
  });
});
