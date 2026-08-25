import { describe, expect, it } from "vitest";
import {
  beijingDateKey,
  formatBeijingDateTime,
  formatBeijingZh,
  formatDateHeadingZh,
  formatTeamStat,
  matchDayZh,
  MARKET_FIELDS,
  MARKET_ZH,
  TEAM_STAT_GROUPS,
  TEAM_STAT_LABELS,
} from "@/components/matches/zh";

describe("TEAM_STAT_GROUPS(2026-08-23 对照 FotMob 分组)", () => {
  it("每个分组的 statKeys 在 TEAM_STAT_LABELS 里都能查到——防止手滑打错 key 后静默消失", () => {
    const labelKeys = new Set(TEAM_STAT_LABELS.map((l) => l.key));
    for (const g of TEAM_STAT_GROUPS) {
      for (const key of g.statKeys) {
        expect(labelKeys.has(key), `分组"${g.label}"里的 key "${key}" 不在 TEAM_STAT_LABELS 里`).toBe(true);
      }
    }
  });

  it("TEAM_STAT_LABELS 的每个字段至少落在一个分组里——新加字段忘记分组会被这条抓到", () => {
    const grouped = new Set(TEAM_STAT_GROUPS.flatMap((g) => g.statKeys));
    const orphans = TEAM_STAT_LABELS.map((l) => l.key).filter((k) => !grouped.has(k));
    expect(orphans).toEqual([]);
  });
});

describe("formatTeamStat(2026-08-25 收敛的球队级统计格式化)", () => {
  it('format:"km" 把米折算成公里,一位小数(121075 → "121.1km")', () => {
    expect(formatTeamStat(121075, { key: "k", label: "l", format: "km" })).toBe("121.1km");
  });

  it('unit 是纯后缀:format:"num" + unit:"次" → "78次"', () => {
    expect(formatTeamStat(78, { key: "k", label: "l", format: "num", unit: "次" })).toBe("78次");
  });

  it("缺失值恒为 —,不折算不补 0", () => {
    expect(formatTeamStat(null, { key: "k", label: "l", format: "km" })).toBe("—");
    expect(formatTeamStat(undefined, { key: "k", label: "l", format: "pct" })).toBe("—");
  });

  it("既有三种 format 行为不变(pct 取整加 %、num1 两位小数、num 取整)", () => {
    expect(formatTeamStat(61.4, { key: "k", label: "l", format: "pct" })).toBe("61%");
    expect(formatTeamStat(2.315, { key: "k", label: "l", format: "num1" })).toBe("2.31");
    expect(formatTeamStat(13.6, { key: "k", label: "l", format: "num" })).toBe("14");
  });
});

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

// 2026-08 修复:比赛详情页曾直接用 date_utc(UTC 自然日)当"比赛日"展示,
// 19:00Z 开球换算成北京时间已经是次日,导致页头和"数据来源与说明"折叠区
// 显示两个不同日期。matchDayZh 是唯一正确入口,判据与
// MatchListLive.tsx::matchDateKey() 完全一致。
describe("matchDayZh(比赛日展示,§6.2.1 精确度纪律)", () => {
  it("精确 kickoff 跨天时换算成北京自然日,isBeijing=true", () => {
    // 2026-08-10T19:00:00Z = 北京 2026-08-11 03:00,与 date_utc(8/10)不同天,
    // 真实复现过的样本形状(赫尔城 vs 曼联同类问题)。
    const r = matchDayZh("2026-08-10T19:00:00Z", "2026-08-10");
    expect(r).toEqual({ text: "2026年8月11日", isBeijing: true });
  });

  it("精确 kickoff 不跨天时仍走换算路径", () => {
    const r = matchDayZh("2026-08-08T02:30:00Z", "2026-08-08");
    expect(r).toEqual({ text: "2026年8月8日", isBeijing: true });
  });

  it("kickoff_at_utc 为 null(§6.2.1 合法情况)回退 date_utc,isBeijing=false——不得凭空 +8h", () => {
    const r = matchDayZh(null, "2026-08-21");
    expect(r).toEqual({ text: "2026年8月21日", isBeijing: false });
  });

  it("kickoff_at_utc 缺省(undefined)同样回退 date_utc", () => {
    const r = matchDayZh(undefined, "2026-08-21");
    expect(r).toEqual({ text: "2026年8月21日", isBeijing: false });
  });

  it("kickoff_at_utc 是 date-only 字符串(防御性,理论上不应发生)时同样回退,不硬换算", () => {
    const r = matchDayZh("2026-08-21", "2026-08-21");
    expect(r).toEqual({ text: "2026年8月21日", isBeijing: false });
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
