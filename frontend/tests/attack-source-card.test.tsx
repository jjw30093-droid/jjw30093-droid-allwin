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
      { label: "运动战", shots: 10, shot_pct: 66.7, xg: 1.2 },
      { label: "反击", shots: 5, shot_pct: 33.3, xg: 0.8 },
    ];
    render(<AttackSourceCard teamName="测试队" rows={rows} note="备注" />);
    expect(screen.getByText(/15 脚射门/)).not.toBeNull();
    expect(screen.getByText(/xG 2\.00/)).not.toBeNull();
  });

  it("某来源 xG 缺失时,卡头不得显示看似完整的合计数字", () => {
    const rows: SourceRow[] = [
      { label: "运动战", shots: 10, shot_pct: 66.7, xg: 1.2 },
      { label: "反击", shots: 5, shot_pct: 33.3, xg: null }, // 缺失,不是 0
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
      { label: "运动战", shots: 10, shot_pct: 66.7, xg: 1.2 },
      { label: "反击", shots: 5, shot_pct: 33.3, xg: null },
    ];
    const { container } = render(<AttackSourceCard teamName="测试队" rows={rows} note="备注" />);
    // 两条成分条(射门 + xG)都在;xG 那条不应该把"运动战"的段拉到 100% 宽度——
    // 那会让读者以为"全部 xG 都来自运动战",而实际上还有一个来源根本没被计入。
    const segs = container.querySelectorAll('[class*="barSeg"]');
    const widths = Array.from(segs).map((el) => (el as HTMLElement).style.width);
    expect(widths).not.toContain("100%");
  });
});
