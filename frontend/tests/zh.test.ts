import { describe, expect, it } from "vitest";
import {
  beijingDateKey,
  formatBeijingDateTime,
  formatBeijingZh,
  formatDateHeadingZh,
  MARKET_FIELDS,
  MARKET_ZH,
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

// 2026-08 审计:bronze_ng_odds_snap 真实存在 66 行 market='corners_ou' 的数据,
// payload 形状与 'ou' 完全一样({line, over, under}),但 MARKET_ZH/MARKET_FIELDS
// 此前没有 "corners_ou" 这个 key,前端拿到真实数据后卡片渲染成空。
describe("corners_ou(角球大小球)市场中文映射", () => {
  it("MARKET_ZH 包含 corners_ou,措辞与后端 backend/queries/odds.py 的 _OPTION_MARKET_LABEL_ZH['corners_ou'] 一致", () => {
    expect(MARKET_ZH.corners_ou).toBe("角球大小");
  });

  it("MARKET_FIELDS.corners_ou 与 MARKET_FIELDS.ou 字段集合一致(payload 形状相同:{line, over, under})", () => {
    expect(MARKET_FIELDS.corners_ou).toEqual(MARKET_FIELDS.ou);
  });
});

// /matches 列表页按开球日期分组(P3.B):分组小标题格式,供 MatchListLive 复用,
// 不在组件里再写第二套日期/星期换算。
describe("formatDateHeadingZh(比赛列表分组小标题)", () => {
  it('"YYYY-MM-DD" → "M月D日 周X"(2026-08-16 是周日,与本次任务描述的示例一致)', () => {
    expect(formatDateHeadingZh("2026-08-16")).toBe("8月16日 周日");
  });

  it("跨月日期同样正确(不做零填充展示)", () => {
    // 2026-08-07 是周五
    expect(formatDateHeadingZh("2026-08-07")).toBe("8月7日 周五");
  });

  it("非法输入原样返回,不抛异常(调用方已经处理过 null,这里只处理格式)", () => {
    expect(formatDateHeadingZh("not-a-date")).toBe("not-a-date");
  });
});
