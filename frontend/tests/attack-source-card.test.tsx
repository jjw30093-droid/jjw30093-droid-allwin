/**
 * AttackSourceCard:某来源 xG 缺失时不得被前端悄悄补 0 再汇总成"看似完整"的
 * 卡头合计,也不能让 xG 占比条只对已知来源归一化到 100%(那会把"已知部分"
 * 画成"全部")——两种呈现都会让读者误以为拿到的是完整数据。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { AttackSourceCard } from "@/components/matches/MatchDataModules";
import type { components } from "@/lib/api-types";

afterEach(cleanup);

type SourceRow = components["schemas"]["MatchPreviewAttackSourceDTO"];

describe("AttackSourceCard 部分 xG 缺失时的诚实展示", () => {
  it("全部来源都有 xG 时,卡头显示真实合计", () => {
    const rows: SourceRow[] = [
      { key: "RegularPlay", label: "运动战", shots: 10, shot_pct: 66.7, xg: 1.2 },
      { key: "FastBreak", label: "反击", shots: 5, shot_pct: 33.3, xg: 0.8 },
    ];
    render(<AttackSourceCard teamName="测试队" rows={rows} note="备注" />);
    expect(screen.getByText(/15 脚射门/)).not.toBeNull();
    expect(screen.getByText(/xG 2\.00/)).not.toBeNull();
  });

  it("某来源 xG 缺失时,卡头不得显示看似完整的合计数字", () => {
    const rows: SourceRow[] = [
      { key: "RegularPlay", label: "运动战", shots: 10, shot_pct: 66.7, xg: 1.2 },
      { key: "FastBreak", label: "反击", shots: 5, shot_pct: 33.3, xg: null }, // 缺失,不是 0
    ];
    const { container } = render(<AttackSourceCard teamName="测试队" rows={rows} note="备注" />);
    // 卡头合计(cardMeta,不是逐条明细里"运动战"自己那行合法的 xG 1.20)
    // 真实合计应至少是 1.2(运动战)+ 未知(反击)—— 把反击的 null 当 0 求和会
    // 显示成"xG 1.20",这是一个看似精确、实则低估的假合计,不能出现。
    const meta = container.querySelector('[class*="cardMeta"]');
    expect(meta?.textContent).not.toMatch(/xG 1\.20/);
    // 必须显式告知"部分来源缺失",不能用一个数字蒙混过去。
    expect(meta?.textContent).toMatch(/部分来源.*缺失/);
  });

  it("某来源 xG 缺失时,xG 占比条不得只对已知来源归一化成 100%", () => {
    const rows: SourceRow[] = [
      { key: "RegularPlay", label: "运动战", shots: 10, shot_pct: 66.7, xg: 1.2 },
      { key: "FastBreak", label: "反击", shots: 5, shot_pct: 33.3, xg: null },
    ];
    const { container } = render(<AttackSourceCard teamName="测试队" rows={rows} note="备注" />);
    // 两条成分条(射门 + xG)都在;xG 那条不应该把"运动战"的段拉到 100% 宽度——
    // 那会让读者以为"全部 xG 都来自运动战",而实际上还有一个来源根本没被计入。
    const segs = container.querySelectorAll('[class*="barSeg"]');
    const widths = Array.from(segs).map((el) => (el as HTMLElement).style.width);
    expect(widths).not.toContain("100%");
  });
});

describe("2026-09 真实缺陷修复:窄色块合并进「其他」", () => {
  // 8 种来源,6 种是 <5% 的碎片(射门和 xG 占比都低),2 种是主力来源。
  const rows: SourceRow[] = [
    { key: "RegularPlay", label: "运动战", shots: 60, shot_pct: 60, xg: 4.0 },
    { key: "FastBreak", label: "反击", shots: 30, shot_pct: 30, xg: 2.0 },
    { key: "FromCorner", label: "角球", shots: 2, shot_pct: 2, xg: 0.1 },
    { key: "SetPiece", label: "定位球", shots: 2, shot_pct: 2, xg: 0.1 },
    { key: "FreeKick", label: "任意球", shots: 2, shot_pct: 2, xg: 0.1 },
    { key: "ThrowInSetPiece", label: "界外球战术", shots: 1, shot_pct: 1, xg: 0.05 },
    { key: "IndividualPlay", label: "个人突破", shots: 2, shot_pct: 2, xg: 0.1 },
    { key: "Penalty", label: "点球", shots: 1, shot_pct: 1, xg: 0.05 },
  ];

  it("两条(射门/xG)都低于阈值的来源合并进「其他」,不再各自占一段", () => {
    const { container } = render(<AttackSourceCard teamName="测试队" rows={rows} note="备注" />);
    const bars = container.querySelectorAll('[class*="bars"] > [class*="barRow"]');
    for (const bar of bars) {
      const segLabels = [...bar.querySelectorAll('[class*="barSeg"]')].map((el) => el.getAttribute("title"));
      // 6 个碎片来源不应该各自出现在 title 里当独立段,应该被"其他"吸收。
      expect(segLabels.some((t) => t?.startsWith("角球"))).toBe(false);
      expect(segLabels.some((t) => t?.startsWith("其他"))).toBe(true);
    }
    // 明细列表(sourceList)仍逐条列出全部 8 种来源,合并只影响条形图,
    // 不影响下方明细——CLAUDE.md §2.2 不做静默丢弃。
    expect(screen.getByText("角球")).not.toBeNull();
    expect(screen.getByText("界外球战术")).not.toBeNull();
    expect(container.querySelectorAll('[class*="sourceRow"]').length).toBe(8);
  });

  it("只在一条(射门或 xG)里低于阈值的来源保持独立,不强行合并", () => {
    // 反击射门占比不低(30%),但 xG 占比很低(0.05/合计≈很小)——
    // 两条必须同时低于阈值才合并,反击不该被合并。
    const mixedRows: SourceRow[] = [
      { key: "RegularPlay", label: "运动战", shots: 60, shot_pct: 85.7, xg: 5.0 },
      { key: "FastBreak", label: "反击", shots: 10, shot_pct: 14.3, xg: 0.02 },
    ];
    const { container } = render(<AttackSourceCard teamName="测试队" rows={mixedRows} note="备注" />);
    const bars = container.querySelectorAll('[class*="bars"] > [class*="barRow"]');
    for (const bar of bars) {
      const segLabels = [...bar.querySelectorAll('[class*="barSeg"]')].map((el) => el.getAttribute("title"));
      expect(segLabels.some((t) => t?.startsWith("反击"))).toBe(true);
      expect(segLabels.some((t) => t?.startsWith("其他"))).toBe(false);
    }
  });

  it("两条形的分段集合必须一致——不能射门条合并了但 xG 条没合并", () => {
    const { container } = render(<AttackSourceCard teamName="测试队" rows={rows} note="备注" />);
    const bars = [...container.querySelectorAll('[class*="bars"] > [class*="barRow"]')];
    const segCounts = bars.map((bar) => bar.querySelectorAll('[class*="barSeg"]').length);
    expect(new Set(segCounts).size).toBe(1);
  });

  it("宽度足够的段(≥15%)直接在段内标中文名,不依赖 hover", () => {
    const { container } = render(<AttackSourceCard teamName="测试队" rows={rows} note="备注" />);
    expect(screen.getAllByText("运动战").length).toBeGreaterThan(0);
    const inlineLabels = container.querySelectorAll('[class*="barSegLabel"]');
    expect(inlineLabels.length).toBeGreaterThan(0);
  });
});
