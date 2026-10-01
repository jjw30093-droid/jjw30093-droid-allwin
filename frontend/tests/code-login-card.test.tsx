/**
 * 公众号发码登录卡片(2026-10,CLAUDE.md §7.3):
 * - 显示后端返回的 4 位验证码、公众号名和三步说明;secret 不出现在 DOM;
 * - 轮询领取成功后:老用户(nickname_set=true)直接跳转;新用户先起昵称,
 *   保存 POST {display_name},跳过 POST {display_name:null},之后才跳转。
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CodeLoginCard } from "@/components/auth/CodeLoginCard";

type Call = { url: string; method: string; body: unknown };

const assign = vi.fn();
const realLocation = window.location;

beforeEach(() => {
  assign.mockReset();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...realLocation, assign },
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  Object.defineProperty(window, "location", { configurable: true, value: realLocation });
});

function mockFetch(opts: { nicknameSet: boolean; claimAfter?: number }): Call[] {
  const calls: Call[] = [];
  let claims = 0;
  const expires = new Date(Date.now() + 300_000).toISOString();
  vi.stubGlobal(
    "fetch",
    vi.fn((input: unknown, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ url, method, body });
      const json = (payload: unknown) =>
        Promise.resolve(
          new Response(JSON.stringify(payload), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      if (url.endsWith("/api/v1/auth/wechat/device")) {
        return json({ request_id: "req-1", secret: "very-secret-value", login_code: "0427", expires_at: expires });
      }
      if (url.includes("/claim")) {
        claims += 1;
        return json({ status: claims > (opts.claimAfter ?? 0) ? "claimed" : "pending" });
      }
      if (url.endsWith("/api/v1/me")) {
        return json({
          authenticated: true,
          user: { id: "u1", display_name: "球迷 ABC123", role: "user", short_code: "ABC123", nickname_set: opts.nicknameSet },
        });
      }
      return json({ status: "ok" });
    }),
  );
  return calls;
}

describe("CodeLoginCard", () => {
  it("显示验证码、公众号名与步骤,secret 不进 DOM", async () => {
    mockFetch({ nicknameSet: true, claimAfter: 99 });
    const { container } = render(<CodeLoginCard nextPath="/account" env="desktop" />);
    const code = await screen.findByTestId("login-code");
    expect(code.textContent).toBe("0427");
    expect(screen.getByText(/用手机微信扫下面的二维码,关注公众号「足球喵喵第」/)).not.toBeNull();
    expect(screen.getByText(/在公众号对话框里发送验证码 0427/)).not.toBeNull();
    expect(screen.getByRole("img", { name: "足球喵喵第 公众号二维码" }).getAttribute("src")).toBe(
      "/brand/wechat-oa-qr.jpg",
    );
    expect(container.innerHTML).not.toContain("very-secret-value");
  });

  it("按环境给关注方式:微信内长按二维码;其它手机浏览器搜名字、不放二维码", async () => {
    mockFetch({ nicknameSet: true, claimAfter: 99 });
    const { unmount } = render(<CodeLoginCard nextPath="/" env="wechat" />);
    await screen.findByTestId("login-code");
    expect(screen.getByText(/长按下面的二维码识别,关注公众号「足球喵喵第」/)).not.toBeNull();
    expect(screen.getByRole("img", { name: "足球喵喵第 公众号二维码" })).not.toBeNull();
    unmount();

    render(<CodeLoginCard nextPath="/" env="mobile" />);
    await screen.findByTestId("login-code");
    expect(screen.getByText(/在微信里搜索并关注公众号「足球喵喵第」/)).not.toBeNull();
    expect(screen.queryByRole("img", { name: /公众号二维码/ })).toBeNull();
  });

  it("老用户:领取成功直接跳转,不弹起昵称", async () => {
    const calls = mockFetch({ nicknameSet: true });
    render(<CodeLoginCard nextPath="/account" env="desktop" />);
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/account"), { timeout: 5000 });
    expect(screen.queryByTestId("nickname-input")).toBeNull();
    const claim = calls.find((c) => c.url.includes("/claim"))!;
    expect(claim.body).toEqual({ secret: "very-secret-value" });
  });

  it("新用户:先起昵称,保存后才跳转", async () => {
    const calls = mockFetch({ nicknameSet: false });
    render(<CodeLoginCard nextPath="/reco" env="mobile" />);
    const input = await screen.findByTestId("nickname-input", {}, { timeout: 5000 });
    expect(assign).not.toHaveBeenCalled();
    const save = screen.getByTestId("nickname-save") as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(input, { target: { value: " 老 王 " } });
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/reco"));
    const post = calls.find((c) => c.url.endsWith("/api/v1/account/profile"))!;
    expect(post.method).toBe("POST");
    expect(post.body).toEqual({ display_name: "老 王" });
  });

  it("新用户:点跳过 POST display_name=null 后跳转", async () => {
    const calls = mockFetch({ nicknameSet: false });
    render(<CodeLoginCard nextPath="/account" env="wechat" />);
    fireEvent.click(await screen.findByTestId("nickname-skip", {}, { timeout: 5000 }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/account"));
    const post = calls.find((c) => c.url.endsWith("/api/v1/account/profile"))!;
    expect(post.body).toEqual({ display_name: null });
  });
});
