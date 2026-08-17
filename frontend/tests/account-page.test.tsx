/**
 * /account 页面测试(2026-08-16 权限口径修正)。
 *
 * 匿名态提示文案此前声称"免费登录后可查看全部联赛资料、模型完整概率、
 * 完整赔率时间线"——这些内容现在对匿名恒可见,登录不解锁任何足球数据,
 * 这句话必须删除或改写。
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AccountPage from "@/app/account/page";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function mockAnonymousMe() {
  const headers = new Headers();
  headers.set("content-type", "application/json");
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ authenticated: false, plan: "free", entitlements: [] }), {
        status: 200,
        headers,
      }),
    ),
  );
}

describe("/account 匿名态:不得声称登录后才能查看联赛资料/概率/赔率时间线", () => {
  it("匿名提示文案不包含'免费登录后可查看全部联赛资料'这类football数据描述", async () => {
    mockAnonymousMe();
    render(<AccountPage />);

    await waitFor(() => expect(screen.queryByText("前往登录")).not.toBeNull());
    expect(screen.queryByText(/全部联赛资料/)).toBeNull();
    expect(screen.queryByText(/模型完整概率/)).toBeNull();
    expect(screen.queryByText(/完整赔率时间线/)).toBeNull();
  });
});
