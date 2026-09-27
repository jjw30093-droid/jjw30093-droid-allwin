/**
 * 服务端(RSC)请求 API 的统一 8 秒超时(2026-09-27,防连接堆积耗尽 API 文件描述符)。
 * serverGet / serverGetOptional 是 frontend 里仅有的两处服务端 fetch;这里用假 fetch(遵守
 * AbortSignal)+ 假计时器验证:挂起的请求 8 秒后被中止并抛出可识别的错误,正常请求不受影响、
 * 计时器不残留、非 2xx 与 404 的既有语义不变。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SERVER_FETCH_TIMEOUT_MS, serverGet, serverGetOptional } from "@/lib/api-v1";

function abortable(signal: AbortSignal): Promise<never> {
  return new Promise((_, reject) => {
    signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
  });
}

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("服务端请求超时", () => {
  it("超时常量是 8 秒", () => {
    expect(SERVER_FETCH_TIMEOUT_MS).toBe(8000);
  });

  it("响应头迟迟不来:8 秒后中止并抛出 timeout 错误(serverGet)", async () => {
    const fetchMock = vi.fn((_url: string, init: RequestInit) => abortable(init.signal as AbortSignal));
    vi.stubGlobal("fetch", fetchMock);
    const p = serverGet("/api/v1/matches");
    const assertion = expect(p).rejects.toThrow("serving API timeout after 8000ms: /api/v1/matches");
    await vi.advanceTimersByTimeAsync(7999);
    expect(vi.getTimerCount()).toBe(1); // 还没到点,计时器还在
    await vi.advanceTimersByTimeAsync(2);
    await assertion;
    expect(vi.getTimerCount()).toBe(0);
  });

  it("响应头到了但响应体读不完:同样 8 秒中止(超时覆盖读 body)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init: RequestInit) => ({
        ok: true,
        status: 200,
        json: () => abortable(init.signal as AbortSignal),
        text: () => abortable(init.signal as AbortSignal),
      })),
    );
    const assertion = expect(serverGet("/api/v1/leagues")).rejects.toThrow("timeout after 8000ms");
    await vi.advanceTimersByTimeAsync(8001);
    await assertion;
  });

  it("serverGetOptional 也有同样的超时", async () => {
    vi.stubGlobal("fetch", vi.fn((_u: string, init: RequestInit) => abortable(init.signal as AbortSignal)));
    const assertion = expect(serverGetOptional("/api/v1/reco/overview")).rejects.toThrow("timeout after 8000ms");
    await vi.advanceTimersByTimeAsync(8001);
    await assertion;
  });

  it("每次请求都带 AbortSignal,并保留原有 revalidate / no-store 缓存参数", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await serverGet("/api/v1/a", { revalidate: 60 });
    await serverGet("/api/v1/b");
    const first = fetchMock.mock.calls[0]![1]!;
    const second = fetchMock.mock.calls[1]![1]!;
    expect(first.signal).toBeInstanceOf(AbortSignal);
    expect((first as { next?: { revalidate: number } }).next).toEqual({ revalidate: 60 });
    expect(second.signal).toBeInstanceOf(AbortSignal);
    expect(second.cache).toBe("no-store");
  });

  it("正常请求不受影响:返回解析后的 JSON,计时器被清除", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ok: 1 }), { status: 200 })));
    await expect(serverGet<{ ok: number }>("/x")).resolves.toEqual({ ok: 1 });
    expect(vi.getTimerCount()).toBe(0);
  });

  it("非 2xx 仍按原语义抛 `serving API <status>`(不是 timeout 错误)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("boom", { status: 500 })));
    await expect(serverGet("/x")).rejects.toThrow("serving API 500: boom");
    await expect(serverGetOptional("/x")).rejects.toThrow("serving API 500");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("401/403/404 的 serverGetOptional 仍返回 null", async () => {
    for (const status of [401, 403, 404]) {
      vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status })));
      await expect(serverGetOptional("/x")).resolves.toBeNull();
    }
  });

  it("网络错误(非超时)原样抛出,不被误报成 timeout", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("fetch failed"); }));
    await expect(serverGet("/x")).rejects.toThrow("fetch failed");
  });
});
