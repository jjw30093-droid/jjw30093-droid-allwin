/**
 * MemberLeagueSection 渲染测试(2026-08-16 权限口径修正)。
 *
 * standings/fixtures/team-stats/players/season-profile 现在对匿名恒 200,
 * 不会再返回 401/403——组件里"未登录/无权限"引导卡片这条分支是死代码,
 * 必须移除;意外的 401/403(不应发生)应当归入通用的加载失败态,而不是
 * 显示一句已经不成立的"登录后即可免费查看该联赛数据"。
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemberLeagueSection } from "@/components/league/MemberLeagueSection";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function mockFetchStatus(status: number, body: unknown = { code: "X", message: "x", details: null }) {
  const headers = new Headers();
  headers.set("content-type", "application/json");
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status, headers })),
  );
}

describe("MemberLeagueSection:401/403 不再是可达状态,不得渲染登录引导文案", () => {
  it("意外收到 401 时不显示'登录后即可免费查看该联赛数据',归入通用错误态(可重试)", async () => {
    mockFetchStatus(401);
    render(<MemberLeagueSection kind="standings" leagueId="47" />);
    await waitFor(() => expect(screen.queryByText("数据暂时无法加载")).not.toBeNull());
    expect(screen.queryByText(/登录后即可免费查看/)).toBeNull();
    expect(screen.queryByText("登录")).toBeNull();
  });

  it("意外收到 403 时同样不显示登录/权益引导文案", async () => {
    mockFetchStatus(403);
    render(<MemberLeagueSection kind="team-stats" leagueId="47" />);
    await waitFor(() => expect(screen.queryByText("数据暂时无法加载")).not.toBeNull());
    expect(screen.queryByText(/无该联赛权限/)).toBeNull();
    expect(screen.queryByText(/免费登录/)).toBeNull();
  });

  it("404 仍然是联赛不存在的诚实说明(未受影响)", async () => {
    mockFetchStatus(404);
    render(<MemberLeagueSection kind="players" leagueId="999999" />);
    await waitFor(() => expect(screen.queryByText("联赛不存在或数据未同步")).not.toBeNull());
  });
});
