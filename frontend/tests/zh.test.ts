import { describe, expect, it } from "vitest";
import {
  beijingDateKey,
  formatBeijingDateTime,
  formatBeijingZh,
} from "@/components/matches/zh";

describe("北京时间格式化", () => {
  it("精确 kickoff 换算为北京时间,含跨天场景", () => {
    // 2026-08-07T17:00:00Z = 北京 2026-08-08 01:00——真实挪超样本
    expect(formatBeijingDateTime("2026-08-07T17:00:00Z")).toBe("2026-08-08 01:00");
    expect(formatBeijingZh("2026-08-07T17:00:00Z")).toBe("8月8日 01:00");
    expect(beijingDateKey("2026-08-07T17:00:00Z")).toBe("2026-08-08");
  });

  it("不跨天的场景保持同一天", () => {
    expect(formatBeijingDateTime("2026-08-08T02:30:00Z")).toBe("2026-08-08 10:30");
    expect(beijingDateKey("2026-08-08T02:30:00Z")).toBe("2026-08-08");
  });

  it("date_only 输入绝不编造时刻,一律返回 null", () => {
    // 英超 2026/2027 全部 380 场目前就是这种形状(kickoff_precision='date_only')
    expect(formatBeijingDateTime("2026-08-21")).toBeNull();
    expect(formatBeijingZh("2026-08-21")).toBeNull();
    expect(beijingDateKey("2026-08-21")).toBeNull();
  });

  it("非法/空输入同样返回 null,不抛异常", () => {
    expect(formatBeijingDateTime("not-a-date")).toBeNull();
    expect(formatBeijingDateTime("")).toBeNull();
    expect(formatBeijingZh("2026-13-99T99:99:00Z")).toBeNull();
  });

  it("带显式时区偏移的输入也能正确换算(不强制要求 Z 结尾)", () => {
    // 与 UTC 17:00 等价
    expect(formatBeijingDateTime("2026-08-07T20:00:00+03:00")).toBe("2026-08-08 01:00");
  });

  it("无时区后缀的裸时间戳按 UTC 处理(与后端 normalize_utc_iso 约定一致)", () => {
    expect(formatBeijingDateTime("2026-08-07T17:00:00")).toBe("2026-08-08 01:00");
  });
});
