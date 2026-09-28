import { describe, expect, it } from "vitest";
import { quadrantIndex } from "@/components/matches/TeamStyleQuadrant";

describe("球队风格象限图 quadrantIndex", () => {
  it("下标按原始数值高低固定,不随方向语义改变", () => {
    const mx = 1, my = 1;
    expect(quadrantIndex({ x: 2, y: 2 }, mx, my)).toBe(0); // x高y高
    expect(quadrantIndex({ x: 2, y: 0 }, mx, my)).toBe(1); // x高y低
    expect(quadrantIndex({ x: 0, y: 2 }, mx, my)).toBe(2); // x低y高
    expect(quadrantIndex({ x: 0, y: 0 }, mx, my)).toBe(3); // x低y低
  });

  it("正好等于均值的点算作\"高\"侧,不会漏画", () => {
    expect(quadrantIndex({ x: 1, y: 1 }, 1, 1)).toBe(0);
  });

  /**
   * 通用真值表回归:quadrantIndex 的原始高低下标 + 一组"y 越低越好"的
   * quadrants 文案组合后,方向语义必须正确,不是"y 高 = 好"这个错误假设
   * (修复前四个标签全部错位)。「攻守 xG」视角本身 2026-09-28 起已改为
   * 复用球队页 quadrantOf/dirsOf(见 tests/team-quadrant-views.test.ts),
   * 这里的 quadrants 文案只是历史遗留的示例数组,用来验证 quadrantIndex
   * 这个仍在被 poss-fastbreak/cross-box 使用的纯函数本身没有问题。
   */
  it("y 反转的方向语义:quadrantIndex 原始高低下标 + 反转文案组合正确", () => {
    const quadrants = ["对攻型", "攻守兼备", "攻守俱弱", "重守轻攻"];
    const mx = 1.5; // 场均预期进球均值
    const my = 1.3; // 场均预期失球均值(越低越好)
    const labelOf = (x: number, y: number) => quadrants[quadrantIndex({ x, y }, mx, my)];

    expect(labelOf(2.0, 1.8)).toBe("对攻型"); // 进球多 + 失球多
    expect(labelOf(2.0, 0.9)).toBe("攻守兼备"); // 进球多 + 失球少(真正的强队)
    expect(labelOf(1.0, 1.8)).toBe("攻守俱弱"); // 进球少 + 失球多(真正的弱队)
    expect(labelOf(1.0, 0.9)).toBe("重守轻攻"); // 进球少 + 失球少
  });

  it("控球×快攻 / 传中×禁区触球 视角(无方向反转)标签保持原有含义", () => {
    const possFastbreak = ["控快兼备", "阵地控球", "防守反击", "控守被动"];
    const mx = 50, my = 10;
    const labelOf = (arr: string[], x: number, y: number) => arr[quadrantIndex({ x, y }, mx, my)];
    expect(labelOf(possFastbreak, 60, 15)).toBe("控快兼备"); // 控球高+快攻占比高
    expect(labelOf(possFastbreak, 60, 5)).toBe("阵地控球"); // 控球高+快攻占比低
    expect(labelOf(possFastbreak, 40, 15)).toBe("防守反击"); // 控球低+快攻占比高
    expect(labelOf(possFastbreak, 40, 5)).toBe("控守被动"); // 控球低+快攻占比低
  });
});
