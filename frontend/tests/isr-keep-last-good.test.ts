/** 首页 ISR 取数失败时保留上一版正常页面(2026-10-02 线上实测:发版重启接口时首页把
 * "今日比赛暂时无法加载"缓存了下来)。生产运行时必须抛出去,Next 才会继续用上一版。 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { keepLastGoodPage } from "@/lib/isr";

afterEach(() => vi.unstubAllEnvs());

describe("keepLastGoodPage", () => {
  it("生产运行时(ISR 重新生成)抛出原错误", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PHASE", "phase-production-server");
    const err = new Error("ECONNREFUSED");
    expect(() => keepLastGoodPage(null)(err)).toThrow(err);
  });
  it("构建期没有上一版可用,退回降级内容", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PHASE", "phase-production-build");
    expect(keepLastGoodPage(null)(new Error("x"))).toBeNull();
  });
  it("开发/测试环境退回降级内容", () => {
    vi.stubEnv("NODE_ENV", "development");
    expect(keepLastGoodPage([] as number[])(new Error("x"))).toEqual([]);
  });
});
