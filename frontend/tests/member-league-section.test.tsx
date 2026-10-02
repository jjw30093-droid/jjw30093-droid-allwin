/**
 * MemberLeagueSection 渲染测试。
 *
 * 2026-10-02 登录门禁:非英超联赛数据对匿名 401 → 直接跳登录页(登录后回到
 * 本页);403 等其他错误归入可重试错误态;404 是联赛不存在的诚实说明。
 * 登录后的客户端加载路径必须与各栏目 SSR 分支同构(赛季速览、球员象限图)。
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemberLeagueSection } from "@/components/league/MemberLeagueSection";
import { redirectToLogin } from "@/lib/login-gate";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/league/87/overview",
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/lib/login-gate", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/login-gate")>()),
  redirectToLogin: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.mocked(redirectToLogin).mockClear();
});

function mockFetchStatus(status: number, body: unknown = { code: "X", message: "x", details: null }) {
  const headers = new Headers();
  headers.set("content-type", "application/json");
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status, headers })),
  );
}

describe("MemberLeagueSection:401 跳登录页,403 归入错误态", () => {
  it("收到 401 时直接跳登录页,保持骨架,不渲染引导卡片", async () => {
    mockFetchStatus(401, { code: "login_required", message: "登录后查看", details: null });
    render(<MemberLeagueSection kind="standings" leagueId="87" />);
    await waitFor(() => expect(redirectToLogin).toHaveBeenCalledTimes(1));
    expect(screen.getByLabelText("联赛数据加载中")).not.toBeNull();
    expect(screen.queryByText("数据暂时无法加载")).toBeNull();
  });

  it("赛季速览栏目登录后渲染赛季速览,不是积分榜", async () => {
    const headers = new Headers({ "content-type": "application/json" });
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          league_id: 87, season: "2025/2026", available_seasons: ["2025/2026"],
          summary: null, goal_minutes: [], score_distribution: [], over_under: [],
          empty_reason: "该联赛该赛季暂无赛季统计数据",
        }),
        { status: 200, headers },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<MemberLeagueSection kind="season-profile" leagueId="87" />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/v1/leagues/87/season-profile");
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/standings"))).toBe(false);
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
