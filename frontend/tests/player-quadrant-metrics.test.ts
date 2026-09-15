/**
 * playerMetrics.ts(2026-09-15,联赛球员象限图):出场占比门槛、PLAYER_METRICS
 * 取值口径、排名/均值的诚实纪律(缺值 null 不补 0)。
 */

import { describe, expect, it } from "vitest";
import type { PlayerQuadrantRow } from "@/lib/api-v1";
import {
  competitionRank,
  formatMetric,
  leagueMean,
  meetsMinutesShare,
  MINUTES_SHARE_THRESHOLD,
  PLAYER_METRICS,
  playerKey,
  positionLabel,
  POSITIONS,
  rankOf,
} from "@/components/league/playerMetrics";

function row(over: Partial<PlayerQuadrantRow> = {}): PlayerQuadrantRow {
  return {
    player: { player_id: "p1", name: "测试球员", name_en: null },
    team: { team_id: 1001, name: "队A", name_en: null, crest_url: null },
    usual_position: 2,
    appearances: 10,
    minutes_played: 900,
    team_minutes: 1800,
    minutes_share: 0.5,
    teams_count: 1,
    ratios: {},
    ...over,
  } as PlayerQuadrantRow;
}

describe("MINUTES_SHARE_THRESHOLD", () => {
  it("是 40%(站长拍板)", () => {
    expect(MINUTES_SHARE_THRESHOLD).toBe(0.4);
  });
});

describe("meetsMinutesShare", () => {
  it("minutes_share 为 null 时判不达标(不能假设够)", () => {
    expect(meetsMinutesShare(row({ minutes_share: null }))).toBe(false);
  });

  it("恰好等于门槛时达标(闭区间)", () => {
    expect(meetsMinutesShare(row({ minutes_share: 0.4 }))).toBe(true);
    expect(meetsMinutesShare(row({ minutes_share: 0.3999 }))).toBe(false);
  });

  it("远高于门槛时达标", () => {
    expect(meetsMinutesShare(row({ minutes_share: 1.0 }))).toBe(true);
  });
});

describe("POSITIONS / positionLabel", () => {
  it("四个位置,实测口径 0=门将 1=后卫 2=中场 3=前锋", () => {
    expect(POSITIONS).toEqual([
      { value: 0, label: "门将" },
      { value: 1, label: "后卫" },
      { value: 2, label: "中场" },
      { value: 3, label: "前锋" },
    ]);
  });

  it("未知位置值返回兜底文案而不是 undefined", () => {
    expect(positionLabel(null)).toBe("未知位置");
    expect(positionLabel(99)).toBe("未知位置");
  });

  it("已知位置值返回对应中文", () => {
    expect(positionLabel(0)).toBe("门将");
    expect(positionLabel(3)).toBe("前锋");
  });
});

describe("playerKey", () => {
  it("有 player_id 时用 id 前缀", () => {
    expect(playerKey({ player_id: "123", name: "张三" })).toBe("id:123");
  });

  it("没有 player_id 时回退用姓名", () => {
    expect(playerKey({ player_id: null, name: "张三" })).toBe("name:张三");
  });
});

describe("PLAYER_METRICS 取值口径", () => {
  it("每个指标都能从 ratios 里正确取值,缺失时返回 null 不补 0", () => {
    const withValue = row({ ratios: { npxg_per90: { value: 0.25, numerator: 1, denominator: 360, paired_matches: 4 } } });
    expect(PLAYER_METRICS.npxgPer90.value(withValue)).toBe(0.25);

    const withoutValue = row({ ratios: {} });
    expect(PLAYER_METRICS.npxgPer90.value(withoutValue)).toBeNull();
  });

  it("门将两个指标(xgotFacedPer90/goalsPreventedPer90)都能正确取值", () => {
    const r = row({
      ratios: {
        xgot_faced_per90: { value: 1.5, numerator: 15, denominator: 900, paired_matches: 10 },
        goals_prevented_per90: { value: 0.3, numerator: 3, denominator: 900, paired_matches: 10 },
      },
    });
    expect(PLAYER_METRICS.xgotFacedPer90.value(r)).toBe(1.5);
    expect(PLAYER_METRICS.goalsPreventedPer90.value(r)).toBe(0.3);
  });

  it("xgotFacedPer90 是 lowerIsBetter(承压越低越好)", () => {
    expect(PLAYER_METRICS.xgotFacedPer90.lowerIsBetter).toBe(true);
  });

  it("finishingDeltaPer90/goalsPreventedPer90 语义是 outcome_variance(短期结果记录)", () => {
    expect(PLAYER_METRICS.finishingDeltaPer90.semantic).toBe("outcome_variance");
    expect(PLAYER_METRICS.goalsPreventedPer90.semantic).toBe("outcome_variance");
  });

  it("10 个指标全部有非空的 caliber 口径说明", () => {
    for (const m of Object.values(PLAYER_METRICS)) {
      expect(m.caliber, `${m.id} 缺 caliber`).toBeTruthy();
    }
  });
});

describe("formatMetric", () => {
  it("null 显示为破折号,不显示 0", () => {
    expect(formatMetric(null, PLAYER_METRICS.npxgPer90)).toBe("—");
  });

  it("按指标的 digits/unit 格式化", () => {
    expect(formatMetric(1.234, PLAYER_METRICS.npxgPer90)).toBe("1.234");
    expect(formatMetric(45.6, PLAYER_METRICS.duelWinRate)).toBe("45.6%");
  });
});

describe("leagueMean / rankOf / competitionRank", () => {
  const rows = [
    row({ player: { player_id: "p1", name: "A", name_en: null }, ratios: { touches_per90: { value: 10, numerator: 100, denominator: 900, paired_matches: 10 } } }),
    row({ player: { player_id: "p2", name: "B", name_en: null }, ratios: { touches_per90: { value: 20, numerator: 200, denominator: 900, paired_matches: 10 } } }),
    row({ player: { player_id: "p3", name: "C", name_en: null }, ratios: {} }),
  ];

  it("leagueMean 分母只数真正有值的球员", () => {
    const m = leagueMean(rows, PLAYER_METRICS.touchesPer90);
    expect(m).toEqual({ mean: 15, n: 2 });
  });

  it("全部缺失时 leagueMean 返回 null", () => {
    const allMissing = rows.map((r) => ({ ...r, ratios: {} }));
    expect(leagueMean(allMissing, PLAYER_METRICS.touchesPer90)).toBeNull();
  });

  it("rankOf 对目标球员自身缺值返回 null(不是排最后)", () => {
    expect(rankOf(rows, PLAYER_METRICS.touchesPer90, "id:p3")).toBeNull();
  });

  it("rankOf 正确计算排名(越高越好,默认降序)", () => {
    expect(rankOf(rows, PLAYER_METRICS.touchesPer90, "id:p2")).toEqual({ rank: 1, total: 2 });
    expect(rankOf(rows, PLAYER_METRICS.touchesPer90, "id:p1")).toEqual({ rank: 2, total: 2 });
  });

  it("competitionRank 并列同名次(1224 制)", () => {
    expect(competitionRank([10, 10, 5], 10)).toEqual({ rank: 1, total: 3 });
    expect(competitionRank([10, 10, 5], 5)).toEqual({ rank: 3, total: 3 });
  });

  it("competitionRank lowerIsBetter 时升序排", () => {
    expect(competitionRank([1, 2, 3], 1, true)).toEqual({ rank: 1, total: 3 });
    expect(competitionRank([1, 2, 3], 3, true)).toEqual({ rank: 3, total: 3 });
  });
});
