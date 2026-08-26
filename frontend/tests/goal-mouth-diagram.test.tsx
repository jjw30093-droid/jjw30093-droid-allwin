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
  BLOCKED_GOAL_MOUTH_TEXT,
  GoalMouthDiagram,
  buildGoalMouthSummary,
  buildOnGoalShotSummary,
  normalizeGoalMouthPoint,
  normalizeOnGoalShot,
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

describe("buildGoalMouthSummary(§11.2 文字摘要,旧路径米数口径)", () => {
  it("框内按米数描述(射手视角,含球门实际尺寸)", () => {
    const text = buildGoalMouthSummary({ gx: 0.5, gz: 0.5 });
    expect(text).toContain("距左门柱 3.66 米");
    expect(text).toContain("离地 1.22 米");
    expect(text).toContain("射手视角");
  });

  it("框外不打印负数米——改说偏出左/右门柱约 X 米、高出横梁约 X 米", () => {
    const left = buildGoalMouthSummary({ gx: -0.5 / 7.32, gz: 0.2 });
    expect(left).toContain("偏出左门柱约 0.50 米");
    const right = buildGoalMouthSummary({ gx: (7.32 + 1.2) / 7.32, gz: 0.2 });
    expect(right).toContain("偏出右门柱约 1.20 米");
    const high = buildGoalMouthSummary({ gx: 0.5, gz: (2.44 + 0.8) / 2.44 });
    expect(high).toContain("高出横梁约 0.80 米");
  });
});

/* ── onGoalShot 主路径(2026-08-26,FotMob 反编译公式) ────────────── */

describe("normalizeOnGoalShot(FotMob onGoalShot 域校验)", () => {
  it("真实宽出 Miss 样本(5868020:x=0.0/y=0.15/zoom=0.32)→ 原样通过", () => {
    expect(normalizeOnGoalShot(0.0, 0.15, 0.32)).toEqual({ x: 0, y: 0.15, zoomRatio: 0.32 });
  });

  it("真实射正样本(zoom 恒为 1.0,x ∈ [0.15,1.5]、y ∈ [0.02,0.55])→ 原样通过", () => {
    expect(normalizeOnGoalShot(1.5, 0.55, 1.0)).toEqual({ x: 1.5, y: 0.55, zoomRatio: 1.0 });
  });

  it("zoomRatio 为 null → 取 1.0(FotMob 客户端反编译实证的默认值)", () => {
    expect(normalizeOnGoalShot(1.0, 0.3, null)).toEqual({ x: 1.0, y: 0.3, zoomRatio: 1.0 });
  });

  it("浮点噪声(-2.2e-16 这种)夹回域内,不当脏数据拒绝", () => {
    const p = normalizeOnGoalShot(-2.2e-16, 0.15, 0.32)!;
    expect(p.x).toBe(0);
    const p2 = normalizeOnGoalShot(2.0, -1e-12, 1.0)!;
    expect(p2.y).toBe(0);
  });

  it("显著出域(x=2.5、y=0.9、zoom=1.5/0/-1)与 NaN/Inf → null,不裁剪(§6.2)", () => {
    expect(normalizeOnGoalShot(2.5, 0.3, 1.0)).toBeNull();
    expect(normalizeOnGoalShot(1.0, 0.9, 1.0)).toBeNull();
    expect(normalizeOnGoalShot(1.0, 0.3, 1.5)).toBeNull();
    expect(normalizeOnGoalShot(1.0, 0.3, 0)).toBeNull();
    expect(normalizeOnGoalShot(1.0, 0.3, -1)).toBeNull();
    expect(normalizeOnGoalShot(Number.NaN, 0.3, 1.0)).toBeNull();
    expect(normalizeOnGoalShot(1.0, Number.POSITIVE_INFINITY, 1.0)).toBeNull();
    expect(normalizeOnGoalShot(null, 0.3, 1.0)).toBeNull();
    expect(normalizeOnGoalShot(1.0, undefined, 1.0)).toBeNull();
  });
});

describe("buildOnGoalShotSummary(§11.2 文字摘要,onGoalShot 定性口径)", () => {
  // [0,2] 横向域到米的换算未被反编译证实——摘要必须定性,不编米数。
  it("宽出 Miss(x=0 < 框左沿 1-z=0.68)→ 偏出左门柱外,不出现编造的米数", () => {
    const text = buildOnGoalShotSummary({ x: 0, y: 0.15, zoomRatio: 0.32 });
    expect(text).toContain("偏出左门柱外");
    expect(text).not.toMatch(/[0-9.]+ 米/);
  });

  it("zoom=1 框内正中半高 → 框内中路、半高", () => {
    const text = buildOnGoalShotSummary({ x: 1.0, y: 0.34, zoomRatio: 1.0 });
    expect(text).toContain("框内中路");
    expect(text).toContain("半高");
  });

  it("y 高于 0.68·z → 高出横梁;x 钉在 2 → 偏出右门柱外", () => {
    expect(buildOnGoalShotSummary({ x: 1.0, y: 0.6, zoomRatio: 0.5 })).toContain("高出横梁");
    expect(buildOnGoalShotSummary({ x: 2.0, y: 0.1, zoomRatio: 0.5 })).toContain("偏出右门柱外");
  });

  it("同场有已验证米数口径的 goal_crossed 时,以括注补充精确米数(两个来源分开措辞)", () => {
    const legacy = normalizeGoalMouthPoint(34, 1.2)!;
    const text = buildOnGoalShotSummary({ x: 1.0, y: 0.34, zoomRatio: 1.0 }, legacy);
    expect(text).toContain("框内中路");
    expect(text).toContain("球门线口径");
    expect(text).toContain("距左门柱 3.66 米");
  });
});

describe("GoalMouthDiagram onGoalShot 主路径渲染(FotMob 公式:画幅缩放、标记对固定画布)", () => {
  const W = 7.32; // 固定画布宽(组件取 W=7.32 使 zoom=1 时画布单位=米)
  const H = W / 3; // = 2.44

  it("宽出 Miss 真实样本(x=0/y=0.15/zoom=0.32):画幅缩至 32% 锚定底边中点,标记在画布左缘", () => {
    const onGoal = normalizeOnGoalShot(0.0, 0.15, 0.32)!;
    const { container } = render(
      <GoalMouthDiagram point={null} onGoal={onGoal} color="#087e78" summary="s" />,
    );
    const frame = container.querySelector("path[data-goal-frame]")!;
    const d = frame.getAttribute("d")!;
    // 画幅左沿 = W(1-z)/2 = 7.32*0.68/2 = 2.4888;顶边 = H(1-z) = 1.6592
    const m = /^M ([\d.]+) ([\d.]+) V ([\d.]+) H ([\d.]+) V ([\d.]+)$/.exec(d)!;
    expect(m).toBeTruthy();
    expect(parseFloat(m[1])).toBeCloseTo((W * (1 - 0.32)) / 2, 3);
    expect(parseFloat(m[2])).toBeCloseTo(H, 3);
    expect(parseFloat(m[3])).toBeCloseTo(H * (1 - 0.32), 3);
    expect(parseFloat(m[4])).toBeCloseTo(W - (W * (1 - 0.32)) / 2, 3);
    // 标记对固定画布度量:cx=(x/2)·W=0、cy=H-(y/0.68)·H
    const marker = container.querySelector("circle")!;
    expect(parseFloat(marker.getAttribute("cx")!)).toBeCloseTo(0, 6);
    expect(parseFloat(marker.getAttribute("cy")!)).toBeCloseTo(H - (0.15 / 0.68) * H, 6);
    expect(marker.getAttribute("fill")).toBe("#087e78");
    expect(marker.getAttribute("stroke")).toBe("var(--ink)"); // 主题感知 token(§11.3)
  });

  it("射正样本(zoom=1):画幅铺满固定画布,两套坐标系重合", () => {
    const onGoal = normalizeOnGoalShot(1.0, 0.34, 1.0)!;
    const { container } = render(
      <GoalMouthDiagram point={null} onGoal={onGoal} color="#087e78" summary="正中" />,
    );
    const d = container.querySelector("path[data-goal-frame]")!.getAttribute("d")!;
    const m = /^M ([\d.]+) ([\d.]+) V ([\d.]+) H ([\d.]+)/.exec(d)!;
    expect(parseFloat(m[1])).toBeCloseTo(0, 6);
    expect(parseFloat(m[3])).toBeCloseTo(0, 6);
    expect(parseFloat(m[4])).toBeCloseTo(W, 6);
    const marker = container.querySelector("circle")!;
    expect(parseFloat(marker.getAttribute("cx")!)).toBeCloseTo(W / 2, 6);
    expect(parseFloat(marker.getAttribute("cy")!)).toBeCloseTo(H / 2, 6);
    expect(container.querySelector("svg")!.getAttribute("aria-label")).toBe("正中");
  });

  it("onGoal 优先于旧路径 point(两者都给时走 FotMob 公式)", () => {
    const onGoal = normalizeOnGoalShot(1.0, 0.34, 1.0)!;
    const legacy = normalizeGoalMouthPoint(30.34, 0)!; // 会画在左柱脚
    const { container } = render(
      <GoalMouthDiagram point={legacy} onGoal={onGoal} color="#087e78" summary="s" />,
    );
    const marker = container.querySelector("circle")!;
    expect(parseFloat(marker.getAttribute("cx")!)).toBeCloseTo(W / 2, 6); // onGoal 的正中
  });

  it("被封堵(blocked)→ 画球门不画标记,文案说被封堵(FotMob 隐藏标记行为)", () => {
    const onGoal = normalizeOnGoalShot(1.0, 0.3, 1.0)!; // 被封堵的球有时也带 onGoalShot,仍须隐藏
    const { container } = render(
      <GoalMouthDiagram point={null} onGoal={onGoal} blocked color="#087e78" summary="s" />,
    );
    expect(container.querySelector("svg")).toBeTruthy();
    expect(container.querySelector("circle")).toBeNull();
    expect(screen.getByText(BLOCKED_GOAL_MOUTH_TEXT)).toBeTruthy();
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
