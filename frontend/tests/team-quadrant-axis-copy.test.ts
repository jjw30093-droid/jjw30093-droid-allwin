/**
 * 象限图"读得懂"改造(2026-09-26):轴端方向提示、大白话轴标题、一句话摘要、
 * 攻防视角新象限名。方向提示的真值表必须逐视角守住——"往哪边读是好"写反了
 * 比不写更糟,而且 style/outcome_variance 轴绝不能出现"越好"(那等于暗示强弱)。
 */

import { describe, expect, it } from "vitest";
import { VIEWS, axisHint, axisTitle, quadrantOf, dirsOf, viewById } from "@/components/league/quadrantViews";

describe("axisHint 方向提示真值表", () => {
  it("攻防视角:与站长要求的逐字文案一致", () => {
    const v = viewById("both-ends");
    expect(axisHint(v.x, "x", v.axisCopy?.x)).toBe("进攻 → 越往右越好");
    expect(axisHint(v.y, "y", v.axisCopy?.y)).toBe("防守 ↑ 越往上越好");
  });

  it("y 轴 lowerIsBetter 时轴已反转,'越好'仍然朝上(不能写成越往下越好)", () => {
    for (const v of VIEWS) {
      if (v.y.lowerIsBetter && v.y.semantic === "performance") {
        expect(axisHint(v.y, "y", v.axisCopy?.y)).toContain("越往上越好");
      }
    }
  });

  it("x 轴 lowerIsBetter 时'好'在左边(防线与门将)", () => {
    const v = viewById("defence-goalkeeping");
    expect(v.x.lowerIsBetter).toBe(true);
    expect(axisHint(v.x, "x", v.axisCopy?.x)).toBe("← 越往左越好");
  });

  it("只有 performance 轴才出现'越好';style / outcome_variance 只写数值大小", () => {
    for (const v of VIEWS) {
      for (const which of ["x", "y"] as const) {
        const axis = v[which];
        const hint = axisHint(axis, which, v.axisCopy?.[which]);
        if (axis.semantic === "performance") expect(hint).toContain("越好");
        else {
          expect(hint).not.toContain("越好");
          expect(hint).toMatch(/数值越(大|小)/);
        }
      }
    }
  });

  it("x 轴提示的箭头方向与'越往X'一致", () => {
    for (const v of VIEWS) {
      const h = axisHint(v.x, "x", v.axisCopy?.x);
      if (h.includes("越往右")) expect(h).toContain("→");
      if (h.includes("越往左")) expect(h).toContain("←");
    }
  });
});

describe("axisTitle 大白话主标签 + 指标副标签", () => {
  it("攻防视角:主标签是大白话,xG/xGA 退为副标签", () => {
    const v = viewById("both-ends");
    expect(axisTitle(v.x, v.axisCopy?.x)).toEqual({ main: "场均创造的机会", sub: "场均预期进球 xG" });
    expect(axisTitle(v.y, v.axisCopy?.y)).toEqual({ main: "场均被对手创造的机会", sub: "场均预期失球 xGA" });
  });

  it("没有 axisCopy 的视角标题与改造前一致(只有主标签)", () => {
    const v = viewById("tactics");
    expect(axisTitle(v.x, v.axisCopy?.x)).toEqual({ main: v.x.label });
  });
});

describe("视角文案", () => {
  it("每个视角都有一句话摘要:单句、不长、不带'已反转'", () => {
    for (const v of VIEWS) {
      expect(v.summary.trim().length).toBeGreaterThan(0);
      expect(v.summary.length).toBeLessThanOrEqual(40);
      expect(v.summary.match(/。/g)?.length).toBe(1);
      expect(v.summary.endsWith("。")).toBe(true);
    }
  });

  it("全部视角的 note 与摘要都不再出现'已反转'(方向由轴端提示表达)", () => {
    for (const v of VIEWS) {
      expect(v.note).not.toContain("已反转");
      expect(v.summary).not.toContain("已反转");
    }
  });

  it("攻防视角象限名改为大白话,且按好/差顺序与 quadrantOf 对上", () => {
    const v = viewById("both-ends");
    expect(v.quadrants).toEqual(["两头都强", "守强攻弱", "两头都弱", "对攻型"]);
    const mx = 1.5, my = 1.5, dirs = dirsOf(v);
    const label = (x: number, y: number) => v.quadrants[quadrantOf({ x, y }, mx, my, dirs)];
    expect(label(2.0, 0.9)).toBe("两头都强"); // 进球多 + 失球少
    expect(label(1.0, 0.9)).toBe("守强攻弱"); // 进球少 + 失球少
    expect(label(1.0, 1.9)).toBe("两头都弱"); // 进球少 + 失球多
    expect(label(2.0, 1.9)).toBe("对攻型"); // 进球多 + 失球多
  });
});
