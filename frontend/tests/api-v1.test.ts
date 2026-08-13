import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiErrorMessage, clientFetch, readCsrfToken } from "@/lib/api-v1";

describe("readCsrfToken", () => {
  it("parses the allwin_csrf cookie", () => {
    document.cookie = "other=1";
    document.cookie = "allwin_csrf=abc%3D123";
    expect(readCsrfToken()).toBe("abc=123");
  });

  it("returns empty string when absent", () => {
    document.cookie = "allwin_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
    expect(readCsrfToken()).toBe("");
  });
});

/** 全站统一错误契约(CLAUDE.md §10):{code, message, details}。clientFetch 的
 * 错误解析路径不再有 .detail,且对非 JSON/空 body/畸形 JSON 有安全回退。 */
describe("clientFetch 错误解析(ApiError)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function mockFetchOnce(status: number, body: BodyInit | null, contentType: string | null) {
    const headers = new Headers();
    if (contentType) headers.set("content-type", contentType);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(body, { status, headers })),
    );
  }

  it("解析规范 ApiErrorDTO:code/message/details 完整保留,不再有 .detail", async () => {
    mockFetchOnce(
      404,
      JSON.stringify({ code: "NOT_FOUND", message: "比赛不存在", details: null }),
      "application/json",
    );
    await expect(clientFetch("/api/v1/matches/999")).rejects.toMatchObject({
      status: 404,
      code: "NOT_FOUND",
      message: "比赛不存在",
      details: null,
    });
    expect("detail" in new ApiError(404, { code: "x", message: "y", details: null })).toBe(false);
  });

  it("领域 code + details.entitlement 不丢失", async () => {
    mockFetchOnce(
      401,
      JSON.stringify({
        code: "membership_required",
        message: "需要 report:deep 权益才能访问该数据",
        details: { entitlement: "report:deep" },
      }),
      "application/json",
    );
    try {
      await clientFetch("/api/league/47/betting");
      throw new Error("should have thrown");
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      const err = e as ApiError;
      expect(err.code).toBe("membership_required");
      expect(err.details).toEqual({ entitlement: "report:deep" });
    }
  });

  it("非 JSON 错误响应:安全回退(code 按状态码,不抛 SyntaxError)", async () => {
    mockFetchOnce(502, "Bad Gateway", "text/plain");
    await expect(clientFetch("/api/v1/x")).rejects.toMatchObject({
      status: 502,
      code: "HTTP_502",
      message: "上游服务暂时不可用",
      details: null,
    });
  });

  it("空 body 错误响应:安全回退,不抛 SyntaxError", async () => {
    mockFetchOnce(500, "", "application/json");
    await expect(clientFetch("/api/v1/x")).rejects.toMatchObject({
      status: 500,
      code: "HTTP_500",
      message: "服务器内部错误",
      details: null,
    });
  });

  it("畸形 JSON 错误响应:安全回退,不抛 SyntaxError", async () => {
    mockFetchOnce(400, "{not valid json", "application/json");
    await expect(clientFetch("/api/v1/x")).rejects.toMatchObject({
      status: 400,
      code: "HTTP_400",
      message: "请求参数有误",
      details: null,
    });
  });

  it("未知状态码回退到通用 message,不崩溃", async () => {
    mockFetchOnce(418, "", "application/json");
    await expect(clientFetch("/api/v1/x")).rejects.toMatchObject({
      status: 418,
      code: "HTTP_418",
      message: "请求失败",
    });
  });
});

describe("apiErrorMessage", () => {
  it("ApiError 优先使用服务端 message", () => {
    const err = new ApiError(404, { code: "NOT_FOUND", message: "比赛不存在", details: null });
    expect(apiErrorMessage(err, "fallback")).toBe("比赛不存在");
  });

  it("非 ApiError(如网络异常)使用调用方 fallback,不再依赖 .detail", () => {
    expect(apiErrorMessage(new TypeError("Failed to fetch"), "网络异常,请稍后重试")).toBe(
      "网络异常,请稍后重试",
    );
    expect(apiErrorMessage("plain string", "fallback")).toBe("fallback");
  });
});
