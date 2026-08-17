/**
 * comparability.ts:窗口 tier 之间的可比性矩阵。
 *
 * 验收返工二(独立复核第二轮,P1):此前的实现把 venue_full 和
 * venue_partial 当成"同一口径"、mixed 和 mixed 也当成"同一口径",允许
 * 生成"谁更高"这类结论。按最新验收契约收紧为只有精确同 tier
 * (venue_full-venue_full / venue_partial-venue_partial)才可比,
 * mixed 不论跟谁比(包括另一个 mixed)都不可比,unavailable 恒不可比。
 */

import { describe, expect, it } from "vitest";
import { tiersComparable } from "@/components/matches/comparability";

describe("tiersComparable(验收返工二收紧后的矩阵)", () => {
  it("venue_full vs venue_full 可比", () => {
    expect(tiersComparable("venue_full", "venue_full")).toBe(true);
  });

  it("venue_partial vs venue_partial 可比", () => {
    expect(tiersComparable("venue_partial", "venue_partial")).toBe(true);
  });

  it("venue_full vs venue_partial 不可比(此前旧实现允许,本轮明确否决)", () => {
    expect(tiersComparable("venue_full", "venue_partial")).toBe(false);
    expect(tiersComparable("venue_partial", "venue_full")).toBe(false);
  });

  it("mixed vs mixed 不可比(此前旧实现允许,本轮明确否决)", () => {
    expect(tiersComparable("mixed", "mixed")).toBe(false);
  });

  it("mixed vs venue_full / venue_partial 不可比", () => {
    expect(tiersComparable("mixed", "venue_full")).toBe(false);
    expect(tiersComparable("mixed", "venue_partial")).toBe(false);
  });

  it("任意 tier vs unavailable 恒不可比", () => {
    expect(tiersComparable("unavailable", "venue_full")).toBe(false);
    expect(tiersComparable("venue_full", "unavailable")).toBe(false);
    expect(tiersComparable("unavailable", "unavailable")).toBe(false);
    expect(tiersComparable("unavailable", "mixed")).toBe(false);
  });
});
