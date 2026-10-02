/**
 * MemberMatchDetail 渲染测试。
 *
 * - 拿到数据 → 渲染 MatchDetailBody;
 * - 401(2026-10-02 登录门禁:未登录看非英超)→ 直接跳登录页,不渲染门禁卡片;
 * - 404(比赛不存在)→ 诚实说明;
 * - 其他错误(403 等)→ 可重试的错误态。
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemberMatchDetail } from "@/components/matches/MemberMatchDetail";
import { redirectToLogin } from "@/lib/login-gate";

vi.mock("@/lib/login-gate", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/login-gate")>()),
  redirectToLogin: vi.fn(),
}));

vi.mock("@/components/matches/MatchDetailBody", () => ({
  MatchDetailBody: () => <div data-testid="match-detail-body-stub" />,
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.mocked(redirectToLogin).mockClear();
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

describe("MemberMatchDetail:401 跳登录页,403 归入错误态", () => {
  it("收到 401 时直接跳登录页,页面保持骨架、不渲染任何门禁卡片", async () => {
    mockDetailStatus(401);
    render(
      <MemberMatchDetail matchId={1} />,
    );
    await waitFor(() => expect(redirectToLogin).toHaveBeenCalledTimes(1));
    expect(screen.getByLabelText("比赛详情加载中")).not.toBeNull();
    expect(screen.queryByText("数据暂时无法加载")).toBeNull();
    expect(screen.queryByText(/登录/)).toBeNull();
  });

  it("意外收到 403 时同样不出现登录门禁卡片", async () => {
    mockDetailStatus(403);
    render(
      <MemberMatchDetail matchId={1} />,
    );
    await waitFor(() => expect(screen.queryByText("数据暂时无法加载")).not.toBeNull());
    expect(screen.queryByText(/登录/)).toBeNull();
  });

  it("404 仍然是比赛不存在的诚实说明(未受影响)", async () => {
    mockDetailStatus(404);
    render(
      <MemberMatchDetail matchId={999999} />,
    );
    await waitFor(() => expect(screen.queryByText("比赛不存在")).not.toBeNull());
  });
});
