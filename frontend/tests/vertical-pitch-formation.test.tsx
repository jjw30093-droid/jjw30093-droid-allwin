/**
 * VerticalPitchFormation(2026-08-25,纵向双队阵容图,赛前/赛后共用)。
 * 取代已退役的横向 PitchFormation(pitch-formation.test.tsx 随之退役)。
 * 坐标映射抽成纯函数单独断言(CLAUDE.md §11.3:聚合/定位逻辑不能只活在
 * 渲染路径里)。
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  VerticalPitchFormation,
  verticalDotPosition,
  pitchLabel,
  type VerticalPitchPlayer,
  type VerticalPitchSide,
} from "@/components/matches/VerticalPitchFormation";

afterEach(cleanup);

function player(overrides: Partial<VerticalPitchPlayer> & { key: string }): VerticalPitchPlayer {
  return {
    avatarId: overrides.key,
    name: "球员",
    shirtNumber: null,
    x: 0.5,
    y: 0.5,
    w: null,
    ...overrides,
  };
}

function side(name: string, players: VerticalPitchPlayer[], formation = "4-3-3"): VerticalPitchSide {
  return { name, formation, players };
}

describe("verticalDotPosition(坐标映射纯函数)", () => {
  it("主队占上半场:门将 y=0.1 贴顶(top 5%),前锋 y=0.87 在中线上方(43.5%)", () => {
    expect(verticalDotPosition(true, 0.5, 0.1)).toEqual({ leftPct: 50, topPct: 5 });
    expect(verticalDotPosition(true, 0.5, 0.87)).toEqual({ leftPct: 50, topPct: 43.5 });
  });

  it("客队占下半场双轴镜像:门将 y=0.1 贴底(95%),前锋 y=0.87 在中线下方(56.5%)", () => {
    const gk = verticalDotPosition(false, 0.5, 0.1);
    expect(gk.topPct).toBe(95);
    const fw = verticalDotPosition(false, 0.5, 0.87);
    expect(fw.topPct).toBeCloseTo(56.5);
  });

  it("客队 x 也镜像(180° 点对称):x=0.125 的左后卫映射到 87.5%", () => {
    // 真实四后卫行坐标 x ∈ {0.125, 0.375, 0.625, 0.875}(库内实测值)
    expect(verticalDotPosition(false, 0.125, 0.357).leftPct).toBe(87.5);
    expect(verticalDotPosition(true, 0.125, 0.357).leftPct).toBe(12.5);
  });
});

describe("pitchLabel(姓名截断,赛前赛后统一)", () => {
  it("拉丁名取姓氏,中文名完整", () => {
    expect(pitchLabel("Marco Bizot")).toBe("Bizot");
    expect(pitchLabel("奥布拉克")).toBe("奥布拉克");
  });
});

describe("VerticalPitchFormation 渲染", () => {
  it("两队同屏各画各的点,球场是竖版整场(viewBox 0 0 68 105)", () => {
    const { container } = render(
      <VerticalPitchFormation
        home={side("主队", [
          player({ key: "h1", name: "主门将", shirtNumber: "1", x: 0.5, y: 0.1 }),
          player({ key: "h2", name: "主前锋", shirtNumber: "9", x: 0.5, y: 0.87 }),
        ])}
        away={side("客队", [player({ key: "a1", name: "客门将", shirtNumber: "1", x: 0.5, y: 0.1 })], "4-4-2")}
        variant="confirmed"
      />,
    );
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("viewBox")).toBe("0 0 68 105");
    expect(container.querySelectorAll('[data-testid="player-avatar-image"] img').length).toBe(3);
    // 主队门将槽位贴上半场顶端,客队门将贴底
    const slots = Array.from(container.querySelectorAll('[class*="slot"]')) as HTMLElement[];
    const tops = slots.map((s) => parseFloat(s.style.top));
    expect(Math.min(...tops)).toBe(5);
    expect(Math.max(...tops)).toBe(95);
  });

  it("预计/确认两档球场变体:data-variant 与容器切换", () => {
    const { container, rerender } = render(
      <VerticalPitchFormation
        home={side("主", [player({ key: "h1" })])}
        away={side("客", [])}
        variant="probable"
      />,
    );
    expect(container.querySelector('[data-variant="probable"]')).not.toBeNull();
    rerender(
      <VerticalPitchFormation
        home={side("主", [player({ key: "h1" })])}
        away={side("客", [])}
        variant="confirmed"
      />,
    );
    expect(container.querySelector('[data-variant="confirmed"]')).not.toBeNull();
  });

  it("槽位宽:显式 w 优先;缺 w 按同行人数推导(四人行→25%)", () => {
    const { container } = render(
      <VerticalPitchFormation
        home={side("主", [
          player({ key: "gk", x: 0.5, y: 0.1, w: 1 }),
          // 四人同一行(y=0.357),不带 w → 每人 25%
          player({ key: "d1", x: 0.125, y: 0.357 }),
          player({ key: "d2", x: 0.375, y: 0.357 }),
          player({ key: "d3", x: 0.625, y: 0.357 }),
          player({ key: "d4", x: 0.875, y: 0.357 }),
        ])}
        away={side("客", [])}
        variant="confirmed"
      />,
    );
    // jsdom 会把 "100.000%" 规范化成 "100%",按数值断言不按字符串
    const widths = (Array.from(container.querySelectorAll('[class*="slot"]')) as HTMLElement[])
      .map((s) => parseFloat(s.style.width))
      .sort((a, b) => a - b);
    expect(widths).toEqual([25, 25, 25, 25, 100]);
  });

  it("单队全员缺坐标 → 该队不画点;两队都缺 → 整图不渲染", () => {
    const { container, rerender } = render(
      <VerticalPitchFormation
        home={side("主", [player({ key: "h1", x: null, y: null })])}
        away={side("客", [player({ key: "a1", x: 0.5, y: 0.1 })])}
        variant="confirmed"
      />,
    );
    expect(container.querySelectorAll('[data-testid="player-avatar-image"]').length).toBe(1);

    rerender(
      <VerticalPitchFormation
        home={side("主", [player({ key: "h1", x: null, y: null })])}
        away={side("客", [player({ key: "a1", x: null, y: null })])}
        variant="confirmed"
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("头像加载失败回退成球衣号文字,不留裂图标", () => {
    const { container } = render(
      <VerticalPitchFormation
        home={side("主", [player({ key: "h1", name: "主门将", shirtNumber: "1", x: 0.5, y: 0.1 })])}
        away={side("客", [])}
        variant="confirmed"
      />,
    );
    const img = container.querySelector('[data-testid="player-avatar-image"] img')!;
    fireEvent.error(img);
    expect(container.querySelector('[data-testid="player-avatar-fallback"]')).not.toBeNull();
    expect(screen.getByText("1")).not.toBeNull();
  });

  it("两端队名+阵型角标(上=主队,下=客队),姓名标签是姓氏", () => {
    render(
      <VerticalPitchFormation
        home={side("Aston Villa", [player({ key: "h1", name: "Marco Bizot", shirtNumber: "40", x: 0.5, y: 0.1 })], "4-2-3-1")}
        away={side("客队", [player({ key: "a1", x: 0.5, y: 0.1 })], "4-4-2")}
        variant="confirmed"
      />,
    );
    expect(screen.getByText(/Aston Villa/)).not.toBeNull();
    expect(screen.getByText(/4-2-3-1/)).not.toBeNull();
    expect(screen.getByText("40 Bizot")).not.toBeNull();
  });
});
