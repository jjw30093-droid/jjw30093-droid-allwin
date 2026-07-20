// @vitest-environment node
/**
 * API 基址解析矩阵(lib/api-base.ts,宪法 §10.3)。
 * node 环境(无 window)覆盖服务端分支;浏览器(jsdom)分支见 api-base.browser.test.ts。
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { clientApiBase, resolveApiBase, serverApiBase } from "@/lib/api-base";

const LOOPBACK = "http://127.0.0.1:8000";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("resolveApiBase:浏览器分支", () => {
  it("NEXT_PUBLIC_API_BASE 非空则用之(development/E2E)", () => {
    expect(
      resolveApiBase({
        isBrowser: true,
        publicBase: "http://127.0.0.1:8010",
        internalBase: undefined,
        serverFallback: LOOPBACK,
      }),
    ).toBe("http://127.0.0.1:8010");
  });

  it("未设置时同源相对 ''(production),绝不使用服务端兜底 127.0.0.1", () => {
    const base = resolveApiBase({
      isBrowser: true,
      publicBase: undefined,
      internalBase: undefined,
      serverFallback: LOOPBACK,
    });
    expect(base).toBe("");
    expect(base).not.toContain("127.0.0.1");
  });

  it("空串 / 全空白视同未设置", () => {
    for (const v of ["", "   ", "\t\n"]) {
      expect(
        resolveApiBase({
          isBrowser: true,
          publicBase: v,
          internalBase: undefined,
          serverFallback: LOOPBACK,
        }),
      ).toBe("");
    }
  });

  it("浏览器端忽略 INTERNAL_API_BASE(服务端内网地址不能进浏览器)", () => {
    expect(
      resolveApiBase({
        isBrowser: true,
        publicBase: undefined,
        internalBase: "http://10.0.0.5:8000",
        serverFallback: LOOPBACK,
      }),
    ).toBe("");
  });

  it("去掉尾部斜杠,避免与 /api/v1 拼出双斜杠", () => {
    expect(
      resolveApiBase({
        isBrowser: true,
        publicBase: "http://127.0.0.1:8010///",
        internalBase: undefined,
        serverFallback: LOOPBACK,
      }),
    ).toBe("http://127.0.0.1:8010");
  });
});

describe("resolveApiBase:服务端分支", () => {
  it("INTERNAL_API_BASE 优先级最高(运行期可调,无需重新构建)", () => {
    expect(
      resolveApiBase({
        isBrowser: false,
        publicBase: "http://public.example",
        internalBase: "http://10.0.0.5:8000",
        serverFallback: LOOPBACK,
      }),
    ).toBe("http://10.0.0.5:8000");
  });

  it("无 INTERNAL_API_BASE 时回退 NEXT_PUBLIC_API_BASE", () => {
    expect(
      resolveApiBase({
        isBrowser: false,
        publicBase: "http://127.0.0.1:8010",
        internalBase: undefined,
        serverFallback: LOOPBACK,
      }),
    ).toBe("http://127.0.0.1:8010");
  });

  it("INTERNAL_API_BASE 为空串/空白时同样回退 NEXT_PUBLIC_API_BASE", () => {
    expect(
      resolveApiBase({
        isBrowser: false,
        publicBase: "http://127.0.0.1:8010",
        internalBase: "  ",
        serverFallback: LOOPBACK,
      }),
    ).toBe("http://127.0.0.1:8010");
  });

  it("都未设置时回退 serverFallback(服务端 localhost 合法)", () => {
    expect(
      resolveApiBase({
        isBrowser: false,
        publicBase: undefined,
        internalBase: undefined,
        serverFallback: LOOPBACK,
      }),
    ).toBe(LOOPBACK);
  });

  it("服务端基址同样去掉尾部斜杠", () => {
    expect(
      resolveApiBase({
        isBrowser: false,
        publicBase: undefined,
        internalBase: "http://127.0.0.1:8000/",
        serverFallback: LOOPBACK,
      }),
    ).toBe("http://127.0.0.1:8000");
  });
});

describe("clientApiBase / serverApiBase:真实读取 process.env", () => {
  it("clientApiBase 读 NEXT_PUBLIC_API_BASE,忽略 INTERNAL_API_BASE", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "http://127.0.0.1:8010");
    vi.stubEnv("INTERNAL_API_BASE", "http://10.0.0.5:8000");
    expect(clientApiBase()).toBe("http://127.0.0.1:8010");
  });

  it("clientApiBase 未设置时为同源相对 ''(即使 INTERNAL_API_BASE 有值)", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "");
    vi.stubEnv("INTERNAL_API_BASE", "http://10.0.0.5:8000");
    expect(clientApiBase()).toBe("");
  });

  it("serverApiBase(node 环境无 window):INTERNAL > NEXT_PUBLIC > 回环默认", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "http://public.example");
    vi.stubEnv("INTERNAL_API_BASE", "http://10.0.0.5:8000");
    expect(serverApiBase()).toBe("http://10.0.0.5:8000");

    vi.stubEnv("INTERNAL_API_BASE", "");
    expect(serverApiBase()).toBe("http://public.example");

    vi.stubEnv("NEXT_PUBLIC_API_BASE", "");
    expect(serverApiBase()).toBe(LOOPBACK);
  });
});
