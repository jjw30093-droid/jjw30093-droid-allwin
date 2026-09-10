/**
 * matchProfile.ts 纯函数测试——联赛百分位画像的定位/格式化/措辞逻辑,
 * 与 backend/metrics/percentile.py 的算法/门槛保持一致(GAP_FLOOR=15、
 * 40/25/15 三档措辞)。
 */

import { describe, expect, it } from "vitest";
import {
  DEFAULT_VISIBLE_MAX,
  GAP_FLOOR,
  decimalsFor,
  dotLeftPct,
  formatMetricValue,
  gapWording,
  groupSectionTitle,
  groupVerdict,
  highlightSentence,
  isStyleMetric,
  metricGap,
  peerSentence,
  profileWindowNote,
  sortMetricsByGap,
  splitMetricsByGap,
  styleGapWording,
  valueDelta,
  type DataProfile,
  type GroupProfile,
  type Highlight,
  type PeerTeam,
} from "@/components/matches/matchProfile";

describe("dotLeftPct", () => {
  it("clamps into 0..100", () => {
    expect(dotLeftPct(50)).toBe(50);
    expect(dotLeftPct(-5)).toBe(0);
    expect(dotLeftPct(150)).toBe(100);
  });
});

describe("formatMetricValue", () => {
  it("formats with unit and digits", () => {
    expect(formatMetricValue(1.934, "球/场", 2)).toBe("1.93球/场");
  });

  it("renders honest empty state for null, not 0", () => {
    expect(formatMetricValue(null, "次/场")).toBe("暂无数据");
  });
});

describe("decimalsFor", () => {
  it("gives xG-like 球 units 2 decimals, others 1", () => {
    expect(decimalsFor("球/场")).toBe(2);
    expect(decimalsFor("次/场")).toBe(1);
    expect(decimalsFor("%")).toBe(1);
  });
});

describe("isStyleMetric", () => {
  it("only semantic=style counts as style", () => {
    expect(isStyleMetric("style")).toBe(true);
    expect(isStyleMetric("performance")).toBe(false);
    expect(isStyleMetric("outcome_variance")).toBe(false);
  });
});

describe("gapWording / styleGapWording", () => {
  it("matches backend percentile.py thresholds (40/25/15)", () => {
    expect(gapWording(45)).toBe("明显更强");
    expect(gapWording(30)).toBe("更强一些");
    expect(gapWording(18)).toBe("略占优势");
    expect(gapWording(5)).toBe("基本持平");
  });

  it("style wording never claims 更强/更弱", () => {
    for (const gap of [45, 30, 18, 5]) {
      expect(styleGapWording(gap)).not.toMatch(/更强|更弱/);
    }
  });
});

function highlight(overrides: Partial<Highlight> = {}): Highlight {
  return {
    key: "xg", name_zh: "预期进球(xG)", home_percentile: 90, away_percentile: 40,
    gap: 50, home_value: 1.9, away_value: 1.1, ...overrides,
  };
}

describe("highlightSentence", () => {
  it("names the leader as whichever side has the higher percentile", () => {
    const s = highlightSentence(highlight({ home_percentile: 20, away_percentile: 80 }), "performance", "主队", "客队");
    expect(s).toContain("客队");
    expect(s).not.toMatch(/^「预期进球\(xG\)」主队/);
  });

  it("performance metric can say 更强, style metric never does", () => {
    const perf = highlightSentence(highlight(), "performance", "主队", "客队");
    const style = highlightSentence(highlight(), "style", "主队", "客队");
    expect(perf).toMatch(/明显更强/);
    expect(style).not.toMatch(/更强/);
    expect(style).toMatch(/明显更偏向/);
  });
});

function metric(overrides: Partial<GroupProfile["metrics"][number]> = {}): GroupProfile["metrics"][number] {
  return {
    key: "xg", name_zh: "预期进球(xG)", unit: "球/场", direction: "higher_better", semantic: "performance",
    home_value: 1.9, away_value: 1.1, home_percentile: 90, away_percentile: 40,
    home_complete: true, away_complete: true, league_sample_size: 18,
    ...overrides,
  };
}

function group(overrides: Partial<GroupProfile> = {}): GroupProfile {
  return {
    key: "attack", title_zh: "攻", metrics: [metric()],
    home_group_percentile: 90, away_group_percentile: 40,
    home_peers: [], away_peers: [],
    ...overrides,
  };
}

describe("metricGap / valueDelta:谁领先 + 多了多少(站长原话:我需要自己去看多了多少)", () => {
  it("领先方按百分位判定,不是按原始值大小", () => {
    // 防守类指标(xGA 越低越好):后端百分位已按 direction 归一化——
    // 原始值 1.21 < 1.61,但百分位 73 > 41,所以主队才是领先方。
    const xga = metric({
      key: "xga", name_zh: "让出预期进球(xGA)", direction: "lower_better",
      home_value: 1.21, away_value: 1.61, home_percentile: 73, away_percentile: 41,
    });
    expect(metricGap(xga)).toEqual({ gap: 32, leader: "home" });
  });

  it("越低越好的指标:队名指向主队,箭头如实向下(原始值更少)", () => {
    const xga = metric({
      key: "xga", name_zh: "让出预期进球(xGA)", unit: "球/场", direction: "lower_better",
      home_value: 1.21, away_value: 1.61, home_percentile: 73, away_percentile: 41,
    });
    const d = valueDelta(xga, "利物浦", "布伦特福德");
    expect(d).toEqual({ leader: "home", leaderName: "利物浦", dir: "down", magnitude: "0.40球/场" });
  });

  it("越高越好的指标:箭头向上", () => {
    const xg = metric({
      unit: "球/场", home_value: 1.69, away_value: 1.22, home_percentile: 64, away_percentile: 41,
    });
    const d = valueDelta(xg, "利物浦", "布伦特福德");
    expect(d?.dir).toBe("up");
    expect(d?.magnitude).toBe("0.47球/场");
    expect(d?.leaderName).toBe("利物浦");
  });

  it("差值从页面显示的那两个数算起,不用后端高精度原始值(否则用户自己一减对不上)", () => {
    // 真实线上案例:后端给 1.6851 / 1.2049,两侧各自显示成 1.69 / 1.22。
    // 用原始值算差是 0.48,用显示值算是 0.47——必须是后者。
    const m = metric({
      unit: "球/场", home_value: 1.6851, away_value: 1.2049,
      home_percentile: 64, away_percentile: 41,
    });
    expect(formatMetricValue(1.6851, "球/场", 2)).toBe("1.69球/场");
    expect(formatMetricValue(1.2049, "球/场", 2)).toBe("1.20球/场");
    expect(valueDelta(m, "主", "客")?.magnitude).toBe("0.49球/场");
  });

  it("两侧显示值四舍五入后相等时不出胶囊(显示上没差就别说有差)", () => {
    const m = metric({ unit: "次/场", home_value: 10.04, away_value: 9.96, home_percentile: 60, away_percentile: 40 });
    expect(valueDelta(m, "主", "客")).toBeNull();
  });

  it("任一侧缺百分位或缺原始值时返回 null,整个胶囊不渲染(不画 0)", () => {
    expect(valueDelta(metric({ home_percentile: null }), "主", "客")).toBeNull();
    expect(valueDelta(metric({ away_value: null }), "主", "客")).toBeNull();
    expect(metricGap(metric({ away_percentile: null }))).toEqual({ gap: null, leader: null });
  });

  it("两侧百分位相同时不判领先方", () => {
    expect(metricGap(metric({ home_percentile: 50, away_percentile: 50 })).leader).toBeNull();
    expect(valueDelta(metric({ home_percentile: 50, away_percentile: 50 }), "主", "客")).toBeNull();
  });
});

describe("sortMetricsByGap / splitMetricsByGap:默认展开哪几行", () => {
  const m = (key: string, hp: number | null, ap: number | null) =>
    metric({ key, home_percentile: hp, away_percentile: ap });

  it("按 |Δ百分位| 降序,缺百分位的排最后(不是差距 0)", () => {
    const rows = [m("a", 50, 45), m("b", null, 40), m("c", 90, 10), m("d", 60, 40)];
    expect(sortMetricsByGap(rows).map((x) => x.key)).toEqual(["c", "d", "a", "b"]);
  });

  it("默认只展开差距 ≥ GAP_FLOOR 的项,上限 4 项", () => {
    const rows = [
      m("a", 90, 10), m("b", 88, 12), m("c", 85, 15), m("d", 80, 20), m("e", 70, 30),
    ];
    const { visibleCount } = splitMetricsByGap(rows);
    expect(visibleCount).toBe(DEFAULT_VISIBLE_MAX);
    expect(rows.every((r) => (metricGap(r).gap ?? 0) >= GAP_FLOOR)).toBe(true);
  });

  it("达标项不足 2 项时补到 2 项,避免默认只剩一行", () => {
    const rows = [m("big", 90, 10), m("tiny", 51, 50), m("tiny2", 50, 49)];
    expect(splitMetricsByGap(rows).visibleCount).toBe(2);
  });

  it("指标总数少于下限时不越界", () => {
    expect(splitMetricsByGap([m("only", 51, 50)]).visibleCount).toBe(1);
  });
});

describe("跨联赛模式(欧战):没有百分位,只并排原始值", () => {
  /** 杯赛模式下后端把两侧百分位都置 null(没有共同参照人群) */
  const raw = (overrides: Partial<GroupProfile["metrics"][number]> = {}) =>
    metric({ home_percentile: null, away_percentile: null, league_sample_size: 0, ...overrides });

  it("百分位全为 null 时仍然出胶囊——联赛模式下这种输入会一个胶囊都不出", () => {
    const m = raw({ unit: "球/场", home_value: 1.69, away_value: 1.22 });
    expect(valueDelta(m, "维京", "拜仁")).toBeNull(); // 不传 mode:沿用联赛口径,无百分位就没有领先方
    expect(valueDelta(m, "维京", "拜仁", "cross_league_raw")).toEqual({
      leader: "home", leaderName: "维京", dir: "up", magnitude: "0.47球/场",
    });
  });

  it("越低越好的指标**不做方向归一化**:胶囊指向原始值更大的一侧", () => {
    // xGA:主队 1.61 > 客队 1.21。联赛模式下"谁更好"由百分位决定(客队更好);
    // 跨联赛模式没有百分位,如果这里按 direction 翻转就等于在说"客队防守更好"
    // ——两队分处不同联赛,这个判断站不住脚,正是站长要求不下的结论。
    const m = raw({
      key: "xga", name_zh: "让出预期进球(xGA)", unit: "球/场", direction: "lower_better",
      home_value: 1.61, away_value: 1.21,
    });
    const d = valueDelta(m, "维京", "拜仁", "cross_league_raw");
    expect(d?.leaderName).toBe("维京");
    expect(d?.dir).toBe("up");
    expect(d?.magnitude).toBe("0.40球/场");
  });

  it("差值仍从页面显示的那两个数算起,不用后端高精度原始值", () => {
    // 第三轮修过的真实 bug,跨联赛模式必须同样成立:后端 1.6851/1.2049,
    // 页面显示 1.69/1.20,用原始值算差是 0.48,用显示值算是 0.49。
    const m = raw({ unit: "球/场", home_value: 1.6851, away_value: 1.2049 });
    expect(valueDelta(m, "主", "客", "cross_league_raw")?.magnitude).toBe("0.49球/场");
  });

  it("两侧显示值四舍五入后相等时不出胶囊", () => {
    const m = raw({ unit: "次/场", home_value: 10.04, away_value: 9.96 });
    expect(valueDelta(m, "主", "客", "cross_league_raw")).toBeNull();
  });

  it("排序按相对差,不是绝对差——单位不同的绝对差不可比", () => {
    // 射门绝对差 3(相对 21%),xG 绝对差 0.6(相对 40%)。按绝对差排会把射门
    // 排前面,按相对差 xG 才该在前。
    const shots = raw({ key: "shots", unit: "次/场", home_value: 16, away_value: 13 });
    const xg = raw({ key: "xg", unit: "球/场", home_value: 1.8, away_value: 1.2 });
    const ordered = sortMetricsByGap([shots, xg], "cross_league_raw");
    expect(ordered.map((m) => m.key)).toEqual(["xg", "shots"]);
  });

  it("两侧都是 0 时分母为 0,排到最后且不产出 NaN", () => {
    const zero = raw({ key: "zero", home_value: 0, away_value: 0 });
    const real = raw({ key: "real", home_value: 1.8, away_value: 1.2 });
    const ordered = sortMetricsByGap([zero, real], "cross_league_raw");
    expect(ordered.map((m) => m.key)).toEqual(["real", "zero"]);
  });

  it("缺原始值的指标排最后", () => {
    const missing = raw({ key: "missing", home_value: null });
    const real = raw({ key: "real", home_value: 1.8, away_value: 1.2 });
    expect(sortMetricsByGap([missing, real], "cross_league_raw").map((m) => m.key))
      .toEqual(["real", "missing"]);
  });

  it("默认展开 4 行——不挪用 GAP_FLOOR(那是百分位门槛)", () => {
    const rows = Array.from({ length: 6 }, (_, i) =>
      raw({ key: `m${i}`, home_value: 10 + i, away_value: 10 }));
    expect(splitMetricsByGap(rows, "cross_league_raw").visibleCount).toBe(DEFAULT_VISIBLE_MAX);
    // 对照:同一批输入在联赛模式下因为百分位全 null,只会展开下限 2 行
    expect(splitMetricsByGap(rows).visibleCount).toBe(2);
  });

  it("指标数少于上限时不越界", () => {
    const rows = [raw({ home_value: 1, away_value: 2 })];
    expect(splitMetricsByGap(rows, "cross_league_raw").visibleCount).toBe(1);
  });

  it("组级结论整行不出——免责只在总览说一次,不在三张卡片各印一遍", () => {
    // 2026-09-10 站长复看:「手机端说明的文字都太多了」。攻/守/控三段各印一句
    // 同样的免责,在手机首屏就是三次重复。这里返回空串,由调用方跳过整行。
    const g = group({ home_group_percentile: null, away_group_percentile: null });
    expect(groupVerdict(g, "performance", "维京", "拜仁", "cross_league_raw")).toBe("");
    // 联赛模式照常出结论
    expect(groupVerdict(group(), "performance", "主队", "客队")).not.toBe("");
  });

  it("窗口说明必须说出「不限赛事」,不能只写「近 N 个主场」", () => {
    const profile: DataProfile = {
      home_matches: 8, away_matches: 10, home_available: true, away_available: true,
      groups: [], highlights: [], unavailable_reason: null,
      comparison_mode: "cross_league_raw", scope_note: "说明",
    };
    const note = profileWindowNote("维京", "拜仁", profile);
    expect(note).toContain("不限赛事");
    expect(note).toContain("近 8 个主场");
  });

  it("联赛模式的窗口说明不带「不限赛事」", () => {
    const profile: DataProfile = {
      home_matches: 10, away_matches: 10, home_available: true, away_available: true,
      groups: [], highlights: [], unavailable_reason: null,
      comparison_mode: "league_percentile", scope_note: null,
    };
    expect(profileWindowNote("主队", "客队", profile)).not.toContain("不限赛事");
  });

  it("段标题去掉「百分位」三字——下面一个分位都没有", () => {
    expect(groupSectionTitle("进攻")).toBe("进攻百分位");
    expect(groupSectionTitle("进攻", "cross_league_raw")).toBe("进攻数据");
  });
});

function peer(overrides: Partial<PeerTeam> = {}): PeerTeam {
  return { team_id: 1, name: "阿森纳", crest_url: null, percentile: 87, ...overrides };
}

describe("peerSentence", () => {
  it("returns null when there are no peers, not a fabricated sentence", () => {
    expect(peerSentence("利物浦", [])).toBeNull();
  });

  it("names peers in the order given (nearest_peers already sorted them), rounds the percentile", () => {
    const peers = [peer({ name: "阿森纳", percentile: 87.4 }), peer({ name: "曼城", percentile: 83 })];
    expect(peerSentence("利物浦", peers)).toBe("利物浦接近阿森纳(87)、曼城(83)");
  });

  it("handles a single peer without a trailing separator", () => {
    expect(peerSentence("布伦特福德", [peer({ name: "狼队", percentile: 32 })])).toBe("布伦特福德接近狼队(32)");
  });
});

describe("groupVerdict", () => {
  it("declines to compare when either side has no group percentile", () => {
    const g = group({ home_group_percentile: null });
    expect(groupVerdict(g, "performance", "主队", "客队")).toMatch(/暂不作整体比较/);
  });

  it("says 基本接近 when gap below floor", () => {
    const g = group({ home_group_percentile: 55, away_group_percentile: 50 });
    expect(groupVerdict(g, "performance", "主队", "客队")).toMatch(/基本接近/);
  });

  it("names the leading side above the floor", () => {
    const g = group({ home_group_percentile: 90, away_group_percentile: 20 });
    const v = groupVerdict(g, "performance", "主队", "客队");
    expect(v).toMatch(/^主队/);
  });
});

describe("profileWindowNote", () => {
  it("reports honest 样本不足 per side instead of a fabricated match count", () => {
    const profile: DataProfile = {
      home_matches: 10, away_matches: 2, home_available: true, away_available: false,
      groups: [], highlights: [], unavailable_reason: null,
      comparison_mode: "league_percentile", scope_note: null,
    };
    const note = profileWindowNote("主队", "客队", profile);
    expect(note).toContain("主队(近 10 个主场)");
    expect(note).toContain("客队(样本不足)");
  });
});
