import { describe, expect, it } from "vitest";
import { readCsrfToken, wechatLoginUrl } from "@/lib/api-v1";

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

describe("wechatLoginUrl", () => {
  it("URL-encodes next path and never auto-navigates", () => {
    const url = wechatLoginUrl("/matches?date=2026-08-21");
    expect(url).toContain("/api/v1/auth/wechat/oa/start?next=");
    expect(url).toContain(encodeURIComponent("/matches?date=2026-08-21"));
  });
});
