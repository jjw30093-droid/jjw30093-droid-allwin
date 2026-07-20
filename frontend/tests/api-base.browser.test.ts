/**
 * API 基址:浏览器(jsdom,存在 window)分支(lib/api-base.ts,宪法 §10.3)。
 * 服务端(node)分支见 api-base.test.ts。
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { clientApiBase, serverApiBase } from "@/lib/api-base";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("浏览器环境(typeof window !== 'undefined')", () => {
  it("clientApiBase:NEXT_PUBLIC_API_BASE 非空则用之(尾斜杠归一)", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "http://127.0.0.1:8010/");
    expect(clientApiBase()).toBe("http://127.0.0.1:8010");
  });

  it("clientApiBase:未设置时同源相对 '',绝不含 127.0.0.1", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "");
    expect(clientApiBase()).toBe("");
  });

  it("serverApiBase 防御:即使被误在浏览器调用,也走浏览器语义,绝不返回回环默认", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "");
    vi.stubEnv("INTERNAL_API_BASE", "http://127.0.0.1:8000");
    expect(serverApiBase()).toBe("");
  });

  it("serverApiBase 在浏览器环境下与 clientApiBase 一致", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "http://127.0.0.1:8010");
    vi.stubEnv("INTERNAL_API_BASE", "http://10.0.0.5:8000");
    expect(serverApiBase()).toBe(clientApiBase());
  });
});
