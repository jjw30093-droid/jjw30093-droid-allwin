/**
 * 资格区图例配置(qualLegend.ts):17 个联赛都要有条目;色值大小写无关;
 * 未配置的联赛/颜色回落"晋级区"。
 */

import { describe, expect, it } from "vitest";
import { QUAL_FALLBACK, QUAL_LEGEND, qualLabel } from "@/components/league/qualLegend";

// backend/queries/leagues.py::LEAGUE_META 的 17 个联赛
const LEAGUE_IDS = [47, 87, 55, 54, 53, 67, 59, 223, 9080, 113, 48, 57, 61, 268, 42, 73, 10216];

describe("qualLegend", () => {
  it("17 个联赛都有配置条目", () => {
    expect(LEAGUE_IDS).toHaveLength(17);
    for (const id of LEAGUE_IDS) expect(QUAL_LEGEND[id], `league ${id}`).toBeTruthy();
  });

  it("配置里没有多余的联赛(与 LEAGUE_META 同一集合)", () => {
    expect(Object.keys(QUAL_LEGEND).map(Number).sort((a, b) => a - b)).toEqual(
      [...LEAGUE_IDS].sort((a, b) => a - b),
    );
  });

  it("配置里的色值都是小写十六进制(比较时统一转小写)", () => {
    for (const table of Object.values(QUAL_LEGEND)) {
      for (const color of Object.keys(table)) expect(color).toMatch(/^#[0-9a-f]{6}$/);
    }
  });

  it("大小写无关;字符串/数字联赛 id 都行", () => {
    expect(qualLabel(47, "#2AD572")).toBe("欧冠区");
    expect(qualLabel("47", "#2ad572")).toBe("欧冠区");
    expect(qualLabel(113, "#C9DD03")).toBe("亚冠区");
  });

  it("联赛未配置 / 颜色未配置 / 缺联赛 id → 回落晋级区", () => {
    expect(QUAL_FALLBACK).toBe("晋级区");
    expect(qualLabel(999999, "#2AD572")).toBe("晋级区");
    expect(qualLabel(47, "#123456")).toBe("晋级区");
    expect(qualLabel(undefined, "#2AD572")).toBe("晋级区");
    expect(qualLabel("abc", "#2AD572")).toBe("晋级区");
  });

  it("冠军不在区间配置里(它是单独一种颜色,仅赛季结束后出现)", () => {
    for (const table of Object.values(QUAL_LEGEND)) {
      for (const text of Object.values(table)) expect(text).not.toContain("冠军");
    }
  });
});
