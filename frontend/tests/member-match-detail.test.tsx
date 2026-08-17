/**
 * MemberMatchDetail 渲染测试(2026-08-16 权限口径修正)。
 *
 * /api/v1/matches/{id} 现在对任何人恒 200,不会再返回 401/403——组件原本
 * "401/403 → LeagueGateCard 登录门禁卡片"这条分支已是死代码,必须移除:
 * - 拿到数据 → 渲染 MatchDetailBody;
 * - 404(比赛不存在)→ 诚实说明;
 * - 其他错误(含理论上不应再出现的 401/403)→ 统一归入可重试的错误态,
 *   绝不出现登录引导文案。
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemberMatchDetail } from "@/components/matches/MemberMatchDetail";

vi.mock("@/components/matches/MatchDetailBody", () => ({
  MatchDetailBody: () => <div data-testid="match-detail-body-stub" />,
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown, status = 200): Response {
  const headers = new Headers();
  headers.set("content-type", "application/json");
  return new Response(JSON.stringify(body), { status, headers });
}

function mockDetailStatus(status: number) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        jsonResponse({ code: "X", message: "x", details: null }, status),
      ),
    ),
  );
}

describe("MemberMatchDetail:401/403 不再是可达状态,不得渲染登录门禁卡片", () => {
  it("意外收到 401 时不出现登录门禁卡片,归入通用错误态(可重试)", async () => {
    mockDetailStatus(401);
    render(
      <MemberMatchDetail matchId={1} returnTo="/matches" returnLabel="比赛列表" />,
    );
    await waitFor(() => expect(screen.queryByText("数据暂时无法加载")).not.toBeNull());
    expect(screen.queryByText(/登录/)).toBeNull();
    expect(screen.queryByText("扫码登录后查看本场数据")).toBeNull();
  });

  it("意外收到 403 时同样不出现登录门禁卡片", async () => {
    mockDetailStatus(403);
    render(
      <MemberMatchDetail matchId={1} returnTo="/matches" returnLabel="比赛列表" />,
    );
    await waitFor(() => expect(screen.queryByText("数据暂时无法加载")).not.toBeNull());
    expect(screen.queryByText(/登录/)).toBeNull();
  });

  it("404 仍然是比赛不存在的诚实说明(未受影响)", async () => {
    mockDetailStatus(404);
    render(
      <MemberMatchDetail matchId={999999} returnTo="/matches" returnLabel="比赛列表" />,
    );
    await waitFor(() => expect(screen.queryByText("比赛不存在")).not.toBeNull());
  });
});
