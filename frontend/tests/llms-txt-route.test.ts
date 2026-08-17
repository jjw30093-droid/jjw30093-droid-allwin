/**
 * /llms.txt 路由测试(2026-08-16 权限口径修正)。
 *
 * 后端已确认:除"每日精选"外,全站比赛内容(含联赛数据、模型完整概率、
 * 完整赔率时间线)对任何人恒可访问,不再有"完整概率/赔率登录后才可查看"
 * 这类登录门禁——面向 AI 爬虫的说明文件不得继续声称这类不存在的门禁。
 */

import { describe, expect, it } from "vitest";
import { GET } from "@/app/llms.txt/route";

describe("GET /llms.txt:不得声称完整概率/赔率需要登录才能查看", () => {
  it("响应体不包含'登录即可查看'/'免费注册'/'需免费注册登录后查看'这类残留措辞", async () => {
    const res = GET();
    const text = await res.text();

    expect(text).not.toMatch(/登录即可查看/);
    expect(text).not.toMatch(/免费注册/);
    expect(text).not.toMatch(/需免费注册登录后查看/);
  });
});
