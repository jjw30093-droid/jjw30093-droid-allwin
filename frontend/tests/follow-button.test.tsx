/**
 * FollowButton:真实写后端的「关注比赛」按钮(2026-08-21)。
 *
 * 原 bug:按钮只写 localStorage,点了显示"已关注"但服务端零调用——账户页
 * 永远是空的(QA 认定的"假成功")。本组测试钉死三条行为红线:
 * - 绝不乐观翻转:服务端确认成功前按钮状态不变;POST 失败必须保持原状态
 *   且出现可见错误(乐观 UI + 静默 catch = 换个形式重犯原 bug);
 * - 匿名点击:展开站内登录引导(不发 POST、不跳转),next 参数正确编码;
 * - 已登录:点击后真的发 POST/DELETE,成功后才翻转。
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  usePathname: () => "/matches/5887595",
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.resetModules();
  window.localStorage.clear();
});

const JSON_HEADERS = new Headers({ "content-type": "application/json" });

type Call = { url: string; method: string };

function stubFetch(opts: {
  getStatus?: number;
  serverIds?: number[];
  mutationStatus?: number;
}): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: unknown, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      calls.push({ url, method });
      if (method === "GET") {
        const status = opts.getStatus ?? 200;
        const payload =
          status === 200
            ? {
                favorites: (opts.serverIds ?? []).map((id) => ({
                  match_id: id,
                  created_at: "2026-08-01T00:00:00Z",
                })),
              }
            : { code: "HTTP_401", message: "需要登录", details: null };
        return Promise.resolve(
          new Response(JSON.stringify(payload), { status, headers: JSON_HEADERS }),
        );
      }
      const status = opts.mutationStatus ?? 200;
      const payload =
        status === 200
          ? { status: "ok" }
          : { code: `HTTP_${status}`, message: "服务器内部错误", details: null };
      return Promise.resolve(
        new Response(JSON.stringify(payload), { status, headers: JSON_HEADERS }),
      );
    }),
  );
  return calls;
}

async function renderButton(matchId = 5887595) {
  const { FollowButton } = await import("@/components/matches/FollowButton");
  return render(<FollowButton matchId={matchId} />);
}

describe("FollowButton:匿名", () => {
  it("点击展开站内登录引导(不发 POST),next 参数正确编码", async () => {
    const calls = stubFetch({ getStatus: 401 });
    await renderButton();
    const btn = await screen.findByRole("button", { name: /关注比赛/ });
    await waitFor(() => expect((btn as HTMLButtonElement).disabled).toBe(false));

    fireEvent.click(btn);
    const link = await screen.findByRole("link", { name: "前往登录" });
    expect(link.getAttribute("href")).toBe(
      `/login?next=${encodeURIComponent("/matches/5887595")}`,
    );
    expect(calls.filter((c) => c.method !== "GET")).toHaveLength(0);

    // "暂不"关闭面板
    fireEvent.click(screen.getByRole("button", { name: "暂不" }));
    expect(screen.queryByRole("link", { name: "前往登录" })).toBeNull();
  });
});

describe("FollowButton:已登录", () => {
  it("初始已在关注列表 → 渲染「✓ 已关注」", async () => {
    stubFetch({ serverIds: [5887595] });
    await renderButton();
    expect(await screen.findByRole("button", { name: /已关注/ })).toBeTruthy();
  });

  it("点击关注 → 发 POST,成功后才翻转为「✓ 已关注」", async () => {
    const calls = stubFetch({ serverIds: [] });
    await renderButton();
    const btn = await screen.findByRole("button", { name: /关注比赛/ });
    await waitFor(() => expect((btn as HTMLButtonElement).disabled).toBe(false));

    fireEvent.click(btn);
    await screen.findByRole("button", { name: /已关注/ });
    expect(calls.some((c) => c.method === "POST" && c.url.includes("/api/v1/favorites"))).toBe(
      true,
    );
  });

  it("POST 500 → 按钮不翻转且出现可见错误(绝不假成功)", async () => {
    stubFetch({ serverIds: [], mutationStatus: 500 });
    await renderButton();
    const btn = await screen.findByRole("button", { name: /关注比赛/ });
    await waitFor(() => expect((btn as HTMLButtonElement).disabled).toBe(false));

    fireEvent.click(btn);
    await screen.findByText(/服务器内部错误|关注失败/);
    expect(screen.queryByRole("button", { name: /已关注/ })).toBeNull();
    expect(screen.getByRole("button", { name: /关注比赛/ })).toBeTruthy();
  });

  it("点击取消关注 → 发 DELETE,成功后翻回「☆ 关注比赛」", async () => {
    const calls = stubFetch({ serverIds: [5887595] });
    await renderButton();
    const btn = await screen.findByRole("button", { name: /已关注/ });
    await waitFor(() => expect((btn as HTMLButtonElement).disabled).toBe(false));

    fireEvent.click(btn);
    await screen.findByRole("button", { name: /关注比赛/ });
    expect(
      calls.some((c) => c.method === "DELETE" && c.url.includes("/api/v1/favorites/5887595")),
    ).toBe(true);
  });
});

describe("FollowButton:占位与不可用", () => {
  it("加载中渲染同尺寸 disabled 占位按钮,不是 null(避免 CLS)", async () => {
    // GET 永不返回 → 一直停在 loading
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    await renderButton();
    const btn = screen.getByRole("button", { name: /关注比赛/ });
    expect((btn as HTMLButtonElement).disabled).toBe(true);
    expect(btn.getAttribute("aria-busy")).toBe("true");
  });
});
