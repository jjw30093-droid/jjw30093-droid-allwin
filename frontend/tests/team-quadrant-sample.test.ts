/**
 * meetsSample / sampleText 真值表(2026-09-14)。门槛挂在指标上,不是视角上——
 * 这两个函数是"该不该把这支球队画上图"的唯一判定入口,必须对缺失字段的行为
 * 保守(缺就是不达标,不能假设够)。
 */

import { describe, expect, it } from "vitest";
import { meetsSample, sampleText, type SampleRule } from "@/components/league/teamMetrics";
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

describe("meetsSample", () => {
  it("没有门槛时总是达标", () => {
    expect(meetsSample(row(), metricWith(undefined))).toBe(true);
  });

  it("matches_played 为 null 时判不达标,不能假设够", () => {
    const m = metricWith({ minMatches: 5 });
    expect(meetsSample(row({ matches_played: null }), m)).toBe(false);
  });

  it("matches_played 恰好等于门槛时达标(闭区间)", () => {
    const m = metricWith({ minMatches: 5 });
    expect(meetsSample(row({ matches_played: 5 }), m)).toBe(true);
    expect(meetsSample(row({ matches_played: 4 }), m)).toBe(false);
  });

  it("minVolume.of 返回 null 时判不达标", () => {
    const m = metricWith({ minVolume: { of: () => null, atLeast: 10, label: "x", unit: "", digits: 0 } });
    expect(meetsSample(row(), m)).toBe(false);
  });

  it("minVolume 恰好等于门槛时达标(闭区间)", () => {
    const m = metricWith({ minVolume: { of: () => 10, atLeast: 10, label: "x", unit: "", digits: 0 } });
    expect(meetsSample(row(), m)).toBe(true);
    const under = metricWith({ minVolume: { of: () => 9.99, atLeast: 10, label: "x", unit: "", digits: 0 } });
    expect(meetsSample(row(), under)).toBe(false);
  });

  it("两条门槛都要过,任一不达标即整体不达标", () => {
    const m = metricWith({
      minMatches: 5,
      minVolume: { of: () => 3, atLeast: 10, label: "x", unit: "", digits: 0 },
    });
    // 场次够但累计量不够
    expect(meetsSample(row({ matches_played: 20 }), m)).toBe(false);
  });
});

describe("sampleText", () => {
  it("没有门槛返回空串", () => {
    expect(sampleText(metricWith(undefined))).toBe("");
  });

  it("只有 minMatches", () => {
    expect(sampleText(metricWith({ minMatches: 8 }))).toBe("不少于 8 场");
  });

  it("只有 minVolume,用 label/unit/digits 拼出人话,不暴露内部字段名", () => {
    const text = sampleText(
      metricWith({ minVolume: { of: () => 1, atLeast: 8, label: "赛季累计非点球 xG", unit: "", digits: 1 } }),
    );
    expect(text).toBe("赛季累计非点球 xG不少于 8.0");
  });

  it("两条门槛都有时用顿号连接", () => {
    const text = sampleText(
      metricWith({
        minMatches: 6,
        minVolume: { of: () => 1, atLeast: 2000, label: "赛季累计成功传球", unit: "次", digits: 0 },
      }),
    );
    expect(text).toBe("不少于 6 场、赛季累计成功传球不少于 2000次");
  });
});
