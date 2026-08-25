/**
 * 球门框示意图(2026-08-25):坐标归一化、文字摘要、渲染分支与标记对比度。
 *
 * normalizeGoalMouthPoint 的坐标语义已对生产 fact_shotmap 真实数值验证
 * (goal_crossed_y=球场 Y、goal_crossed_z=离地米数,见组件注释),这里断言
 * 的是换算公式与"合理域外一律 null"的诚实纪律(CLAUDE.md §6.2)。
 *
 * 对比度断言的背景是**面板底色 --surface**(浅 #ffffff / 深 #0d2029),
 * 不是射门图的球场底色——两份 fixture 不能混用(方案 §5.4)。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  GoalMouthDiagram,
  buildGoalMouthSummary,
  normalizeGoalMouthPoint,
} from "@/components/matches/GoalMouthDiagram";
import { contrastRatioHex, MIN_CONTRAST } from "@/components/charts/colorContrast";

afterEach(cleanup);

describe("normalizeGoalMouthPoint(已验证坐标语义 → 球门框归一化)", () => {
  it("球门左柱(y=30.34)→ gx=0;右柱(y=37.66)→ gx=1;横梁高(2.44m)→ gz=1", () => {
    expect(normalizeGoalMouthPoint(30.34, 0)).toEqual({ gx: 0, gz: 0 });
    const right = normalizeGoalMouthPoint(37.66, 2.44)!;
    expect(right.gx).toBeCloseTo(1, 10);
    expect(right.gz).toBeCloseTo(1, 10);
  });

  it("真实射正样本形状(y=34、z=1.2)落在框内", () => {
    const p = normalizeGoalMouthPoint(34, 1.2)!;
    expect(p.gx).toBeCloseTo(0.5, 3);
    expect(p.gz).toBeCloseTo(1.2 / 2.44, 10);
  });

  it("输入缺失(null/undefined)→ null,不补 0", () => {
    expect(normalizeGoalMouthPoint(null, 1)).toBeNull();
    expect(normalizeGoalMouthPoint(34, undefined)).toBeNull();
  });

  it("合理域外(y ∉ [0,68] / z ∉ [0,10])→ null,不裁剪", () => {
    expect(normalizeGoalMouthPoint(-1, 1)).toBeNull();
    expect(normalizeGoalMouthPoint(69, 1)).toBeNull();
    expect(normalizeGoalMouthPoint(34, -0.1)).toBeNull();
    expect(normalizeGoalMouthPoint(34, 10.5)).toBeNull();
    expect(normalizeGoalMouthPoint(Number.NaN, 1)).toBeNull();
  });

  it("框外但仍在球场域内的 Miss(y=40)→ 正常归一化(gx>1),由渲染层决定画不画", () => {
    const p = normalizeGoalMouthPoint(40, 1)!;
    expect(p.gx).toBeGreaterThan(1);
  });
});

describe("buildGoalMouthSummary(§11.2 文字摘要)", () => {
  it("按米数描述(射手视角,含球门实际尺寸)", () => {
    const text = buildGoalMouthSummary({ gx: 0.5, gz: 0.5 });
    expect(text).toContain("距左门柱 3.66 米");
    expect(text).toContain("离地 1.22 米");
    expect(text).toContain("射手视角");
  });
});

describe("GoalMouthDiagram 渲染分支", () => {
  it("point=null → 诚实空态文案,不画框不画点", () => {
    const { container } = render(
      <GoalMouthDiagram point={null} color="#087e78" summary="" />,
    );
    expect(screen.getByText("该次射门没有可靠的入网位置数据。")).toBeTruthy();
    expect(container.querySelector("svg")).toBeNull();
  });

  it("框内穿越点 → 画出 svg + 球队色标记圆,aria-label 是摘要", () => {
    const point = normalizeGoalMouthPoint(34, 1.2)!;
    const summary = buildGoalMouthSummary(point);
    const { container } = render(
      <GoalMouthDiagram point={point} color="#087e78" summary={summary} />,
    );
    const svg = container.querySelector("svg")!;
    expect(svg).toBeTruthy();
    expect(svg.getAttribute("aria-label")).toBe(summary);
    const marker = container.querySelector("circle")!;
    expect(marker.getAttribute("fill")).toBe("#087e78");
    // 标记描边:主题感知 token,不是硬编码十六进制(§11.3)
    expect(marker.getAttribute("stroke")).toBe("var(--ink)");
  });

  it("框外点 + outsideFrame=false(声称射正却在框外,矛盾数据)→ 空态", () => {
    const point = normalizeGoalMouthPoint(38.5, 1)!; // gx ≈ 1.11,框外
    const { container } = render(
      <GoalMouthDiagram point={point} color="#087e78" summary="s" />,
    );
    expect(container.querySelector("svg")).toBeNull();
  });

  it("框外点 + outsideFrame=true(Miss/Post)且在留白范围内 → 照画", () => {
    const point = normalizeGoalMouthPoint(38.5, 1)!;
    const { container } = render(
      <GoalMouthDiagram point={point} color="#087e78" summary="s" outsideFrame />,
    );
    expect(container.querySelector("svg")).toBeTruthy();
  });

  it("远超留白范围的 Miss(画不下)→ 空态,不裁剪进框里假装精确", () => {
    const point = normalizeGoalMouthPoint(62, 1)!; // gx ≈ 4.3,远在 viewBox 外
    const { container } = render(
      <GoalMouthDiagram point={point} color="#087e78" summary="s" outsideFrame />,
    );
    expect(container.querySelector("svg")).toBeNull();
  });
});

describe("标记对比度(vs 面板底色 --surface,非球场底色)", () => {
  // 与 frontend/app/globals.css 保持同步(同 shot-map-contrast 的有意摩擦)
  const THEMES = [
    { label: "浅色", surface: "#ffffff", teal: "#087e78", navy: "#1d6f8b" },
    { label: "深色", surface: "#0d2029", teal: "#45b9af", navy: "#69b6ce" },
  ];

  it.each(THEMES)("$label:球队回退色标记 vs --surface ≥ 3:1", ({ surface, teal, navy }) => {
    expect(contrastRatioHex(teal, surface)).toBeGreaterThanOrEqual(MIN_CONTRAST);
    expect(contrastRatioHex(navy, surface)).toBeGreaterThanOrEqual(MIN_CONTRAST);
  });
});
