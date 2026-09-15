/**
 * 球队数据页"最近 N 场/主客场"筛选(2026-09-14)对象限图取数层的影响:
 * windowScaleFor() 把筛选状态换算成缩放系数、meetsSample/sampleText 按这个
 * 系数缩小门槛、filterWindowLabel() 生成筛选感知的"虚线是……平均值"文案。
 */

import { describe, expect, it } from "vitest";
import { meetsSample, sampleText, windowScaleFor, type SampleRule } from "@/components/league/teamMetrics";
import { filterWindowLabel } from "@/components/league/quadrantViews";
import type { TeamSeasonStatRow } from "@/lib/api-v1";

function row(over: Partial<TeamSeasonStatRow> = {}): TeamSeasonStatRow {
  return {
    team: { team_id: 1, name: "队A", name_en: null, crest_url: null },
    matches_played: 10,
    ...over,
  } as TeamSeasonStatRow;
}

function metricWith(sample: SampleRule | undefined): { sample?: SampleRule } {
  return { sample };
}

describe("windowScaleFor", () => {
  it("都不筛时返回 1(不缩放)", () => {
    expect(windowScaleFor(undefined, undefined)).toBe(1);
    expect(windowScaleFor(null, "all")).toBe(1);
  });

  it("有 recency 时按 recency/38 缩放,忽略 venue", () => {
    expect(windowScaleFor(5, "all")).toBeCloseTo(5 / 38);
    expect(windowScaleFor(5, "home")).toBeCloseTo(5 / 38); // recency 优先于 venue
    expect(windowScaleFor(3, undefined)).toBeCloseTo(3 / 38);
    expect(windowScaleFor(10, undefined)).toBeCloseTo(10 / 38);
  });

  it("只筛主/客场(没有 recency)时用 0.5 近似", () => {
    expect(windowScaleFor(undefined, "home")).toBe(0.5);
    expect(windowScaleFor(null, "away")).toBe(0.5);
  });
});

describe("meetsSample 的 windowScale 参数", () => {
  it("缺省 windowScale 时行为与筛选功能上线前逐字节相同", () => {
    const m = metricWith({ minMatches: 6 });
    expect(meetsSample(row({ matches_played: 6 }), m)).toBe(true);
    expect(meetsSample(row({ matches_played: 5 }), m)).toBe(false);
  });

  it("minMatches 按 windowScale 等比缩小,四舍五入且至少为 1", () => {
    const m = metricWith({ minMatches: 6 });
    const scale = 5 / 38; // 筛"最近 5 场"
    // 6 * (5/38) ≈ 0.79 → round → 1,不是 0
    expect(meetsSample(row({ matches_played: 1 }), m, scale)).toBe(true);
    expect(meetsSample(row({ matches_played: 0 }), m, scale)).toBe(false);
  });

  it("minVolume.atLeast 按 windowScale 等比缩小(不取整,量本身是连续值)", () => {
    const m = metricWith({
      minVolume: { of: () => 260, atLeast: 2000, label: "x", unit: "次", digits: 0 },
    });
    const scale = 5 / 38; // 2000 * 5/38 ≈ 263.2
    expect(meetsSample(row(), m, scale)).toBe(false); // 260 < 263.2
    expect(meetsSample(row(), { ...m, sample: { ...m.sample, minVolume: { ...m.sample!.minVolume!, of: () => 300 } } }, scale)).toBe(true);
  });

  it("windowScale=1(未筛选)与缺省参数结果一致", () => {
    const m = metricWith({ minMatches: 6, minVolume: { of: () => 2000, atLeast: 2000, label: "x", unit: "", digits: 0 } });
    expect(meetsSample(row({ matches_played: 6 }), m, 1)).toBe(meetsSample(row({ matches_played: 6 }), m));
  });
});

describe("sampleText 的 windowScale 参数", () => {
  it("缺省 windowScale 时文案与筛选功能上线前逐字节相同", () => {
    expect(sampleText(metricWith({ minMatches: 8 }))).toBe("不少于 8 场");
  });

  it("按 windowScale 缩小后的门槛数字要出现在文案里", () => {
    const scale = 5 / 38;
    const text = sampleText(metricWith({ minMatches: 6 }), scale);
    // 6 * 5/38 ≈ 0.79 → round → 1
    expect(text).toBe("不少于 1 场");
  });

  it("minVolume 缩放后的门槛数字按原有 digits 格式化", () => {
    const scale = 5 / 38;
    const text = sampleText(
      metricWith({ minVolume: { of: () => 1, atLeast: 2000, label: "赛季累计成功传球", unit: "次", digits: 0 } }),
      scale,
    );
    expect(text).toBe("赛季累计成功传球不少于 263次");
  });
});

describe("filterWindowLabel", () => {
  it("都不筛时是「本联赛本赛季」(与筛选功能上线前文案一致)", () => {
    expect(filterWindowLabel(undefined, undefined)).toBe("本联赛本赛季");
    expect(filterWindowLabel(null, "all")).toBe("本联赛本赛季");
  });

  it("只筛 recency", () => {
    expect(filterWindowLabel(5, undefined)).toBe("最近5场");
    expect(filterWindowLabel(3, "all")).toBe("最近3场");
  });

  it("只筛 venue", () => {
    expect(filterWindowLabel(undefined, "home")).toBe("主场");
    expect(filterWindowLabel(null, "away")).toBe("客场");
  });

  it("recency 与 venue 同时筛时拼在一起", () => {
    expect(filterWindowLabel(10, "home")).toBe("最近10场主场");
    expect(filterWindowLabel(3, "away")).toBe("最近3场客场");
  });
});
