import { describe, expect, it } from "vitest";
import { summarizeMomentum } from "@/components/matches/MomentumChart";

describe("summarizeMomentum(2026-08-23 势头图纯逻辑)", () => {
  it("按 minute 排序,不信任调用方传入的顺序", () => {
    const { points } = summarizeMomentum([
      { minute: 63, value: 27 },
      { minute: 0, value: 0 },
      { minute: 45.5, value: -62 },
    ]);
    expect(points.map((p) => p.minute)).toEqual([0, 45.5, 63]);
  });

  it("endMinute 取真实最大分钟与 90 的较大值", () => {
    expect(summarizeMomentum([{ minute: 0, value: 0 }]).endMinute).toBe(90);
    expect(summarizeMomentum([{ minute: 93.5, value: 5 }]).endMinute).toBe(93.5);
  });

  it("homeShare/awayShare 分别统计正值/负值的分钟数,0 不计入任何一方", () => {
    const { homeShare, awayShare } = summarizeMomentum([
      { minute: 1, value: 5 },
      { minute: 2, value: -22 },
      { minute: 3, value: 0 },
      { minute: 4, value: 16 },
    ]);
    expect(homeShare).toBe(2);
    expect(awayShare).toBe(1);
  });

  it("空数组不崩溃,endMinute 回退到 90", () => {
    expect(summarizeMomentum([])).toEqual({
      points: [],
      endMinute: 90,
      homeShare: 0,
      awayShare: 0,
    });
  });
});
