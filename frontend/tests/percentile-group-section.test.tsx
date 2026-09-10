/**
 * PercentileGroupSection:替代渲染冒烟测试(CSS 定位,不走 ECharts,不适用
 * SSR 渲染冒烟范式)——断言点的 CSS `left` 百分比直接等于百分位值(锁死
 * "不再按 max(主,客) 归一化"这条修复,回归旧 bug 会导致这条断言失败),
 * `percentile == null` 的一侧不渲染点而不是画在 0/50 这类会被误读成真实
 * 测量值的位置。
 *
 * 2026-09:站长反馈"轴上两个纯色点分不清哪个是哪队"——点改成队徽
 * (CrestDot 包 TeamBadge)。选择器相应从 `[class*="dot"]`(会漏配大小写,
 * "crestDotHome" 里是大写 "Dot")改成精确匹配新类名;新增断言确认队徽
 * 真的带得上"是哪队"这个信息(无 crestUrl 时走 TeamBadge 的中文首字
 * 降级,fallback 元素的可访问文本里必须能看出队名)。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { PercentileGroupSection } from "@/components/matches/PercentileGroupSection";
import type { GroupProfile } from "@/components/matches/matchProfile";

afterEach(cleanup);

function metric(overrides: Partial<GroupProfile["metrics"][number]> = {}): GroupProfile["metrics"][number] {
  return {
    key: "xg", name_zh: "预期进球(xG)", unit: "球/场", direction: "higher_better", semantic: "performance",
    home_value: 1.9, away_value: 1.1, home_percentile: 87, away_percentile: 34,
    home_complete: true, away_complete: true, league_sample_size: 18,
    ...overrides,
  };
}

function group(overrides: Partial<GroupProfile> = {}): GroupProfile {
  return {
    key: "attack", title_zh: "攻", metrics: [metric()],
    home_group_percentile: 87, away_group_percentile: 34,
    home_peers: [], away_peers: [],
    ...overrides,
  };
}

describe("PercentileGroupSection", () => {
  it("positions crest dots at the percentile value, not at value/max", () => {
    const { container } = render(
      <PercentileGroupSection title="进攻百分位" windowNote="窗口说明" homeName="伯恩茅斯" awayName="布伦特福德" group={group()} />,
    );
    const dots = container.querySelectorAll('[class*="crestDotHome"], [class*="crestDotAway"]');
    expect(dots.length).toBe(2);
    const styles = Array.from(dots).map((d) => (d as HTMLElement).style.left);
    expect(styles).toContain("87%");
    expect(styles).toContain("34%");
    // 旧 bug 的归一化结果会是 100%/58.5%(按 max 归一化)——确认不是这个值。
    expect(styles).not.toContain("100%");
  });

  it("crest dot carries which team it is (无 crestUrl 时走队名首字降级),不是一个纯色点", () => {
    render(
      <PercentileGroupSection title="进攻百分位" windowNote="窗口说明" homeName="伯恩茅斯" awayName="布伦特福德" group={group()} />,
    );
    // 无 crestUrl 时 TeamBadge 降级成队名首字——这本身就是"能认出哪队"
    // 这件事的最低限度证明:纯色点做不到,队徽(或降级文字)可以。
    // 2026-09 第三轮起同一枚队徽出现两次(轴上的坐标点 + 数值块前),
    // 所以用 getAllByText:两处都必须有,不是"只要有一处"。
    expect(screen.getAllByText("伯恩").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("布伦").length).toBeGreaterThanOrEqual(2);
  });

  it("does not render a dot when percentile is null, and says so honestly", () => {
    const g = group({ metrics: [metric({ home_percentile: null, away_percentile: null })] });
    render(
      <PercentileGroupSection title="进攻百分位" windowNote="窗口说明" homeName="伯恩茅斯" awayName="布伦特福德" group={g} />,
    );
    const dots = document.querySelectorAll('[class*="crestDotHome"], [class*="crestDotAway"]');
    expect(dots.length).toBe(0);
    // 数字仍然如实展示(不是整行隐藏),只是没有可比的联赛百分位——
    // 措辞落在 aria-label 里(role="img" 的轴本身没有可读子文本)。
    const axis = screen.getByRole("img");
    expect(axis.getAttribute("aria-label")).toMatch(/暂无联赛百分位/);
    expect(screen.getByText("1.90球/场")).toBeTruthy();
  });

  it("shows raw values with unit alongside the percentile, not just the abstract position", () => {
    render(
      <PercentileGroupSection title="进攻百分位" windowNote="窗口说明" homeName="伯恩茅斯" awayName="布伦特福德" group={group()} />,
    );
    // unit "球/场" 走 2 位小数(decimalsFor),1.9/1.1 显示为 1.90/1.10。
    expect(screen.getByText("1.90球/场")).toBeTruthy();
    expect(screen.getByText("1.10球/场")).toBeTruthy();
  });

  it("renders an honest empty row when both sides lack the metric, not a zero-width bar", () => {
    const g = group({ metrics: [metric({ home_value: null, away_value: null, home_percentile: null, away_percentile: null })] });
    render(<PercentileGroupSection title="进攻百分位" windowNote="窗口说明" homeName="伯恩茅斯" awayName="布伦特福德" group={g} />);
    expect(screen.getByText(/两队近期同主客场比赛都无该项数据/)).toBeTruthy();
  });
});

describe("2026-09 第三轮:差值胶囊与领先方强调", () => {
  const render1 = (m: GroupProfile["metrics"][number]) =>
    render(
      <PercentileGroupSection
        title="进攻百分位" windowNote="窗口说明" homeName="伯恩茅斯" awayName="布伦特福德"
        group={group({ metrics: [m] })}
      />,
    );

  it("直接写出谁领先、多多少——不再让读者自己心算", () => {
    render1(metric({ unit: "球/场", home_value: 1.69, away_value: 1.22, home_percentile: 64, away_percentile: 41 }));
    const chip = document.querySelector('[class*="deltaChip"]');
    expect(chip).not.toBeNull();
    expect(chip?.textContent).toContain("伯恩茅斯");
    expect(chip?.textContent).toContain("↑0.47球/场");
  });

  it("越低越好的指标:队名指向百分位高的一方,箭头如实向下(原始值更少)", () => {
    // xGA:主队原始值 1.21 < 客队 1.61,但百分位 73 > 41 —— 主队才是领先方。
    render1(metric({
      key: "xga", name_zh: "让出预期进球(xGA)", unit: "球/场", direction: "lower_better",
      home_value: 1.21, away_value: 1.61, home_percentile: 73, away_percentile: 41,
    }));
    const chip = document.querySelector('[class*="deltaChip"]');
    expect(chip?.textContent).toContain("伯恩茅斯");
    expect(chip?.textContent).toContain("↓0.40球/场");
    // 方向色跟着领先方走(主队 teal),不是恒定的"好=绿"。
    expect(chip?.getAttribute("data-side")).toBe("home");
  });

  it("领先侧带 data-leader,落后侧不带——颜色强调有据可依", () => {
    render1(metric({ home_value: 1.69, away_value: 1.22, home_percentile: 64, away_percentile: 41 }));
    const sides = [...document.querySelectorAll('[class*="side"][data-side]')];
    const home = sides.find((s) => s.getAttribute("data-side") === "home");
    const away = sides.find((s) => s.getAttribute("data-side") === "away");
    expect(home?.getAttribute("data-leader")).toBe("true");
    expect(away?.getAttribute("data-leader")).toBeNull();
  });

  it("任一侧缺百分位时整个胶囊不渲染(不画 0)", () => {
    render1(metric({ home_percentile: null }));
    expect(document.querySelector('[class*="deltaChip"]')).toBeNull();
  });
});

describe("2026-09 第三轮:默认只展开差距明显的项", () => {
  const m = (key: string, hp: number, ap: number) =>
    metric({ key, name_zh: `指标${key}`, home_percentile: hp, away_percentile: ap });

  it("超过 4 项达标时默认只渲染 4 行,其余进默认收起的 <details>", () => {
    const metrics = [m("a", 95, 5), m("b", 90, 10), m("c", 85, 15), m("d", 80, 20), m("e", 75, 25), m("f", 70, 30)];
    const { container } = render(
      <PercentileGroupSection
        title="进攻百分位" windowNote="窗口说明" homeName="伯恩茅斯" awayName="布伦特福德"
        group={group({ metrics })}
      />,
    );
    const details = container.querySelector("details");
    expect(details).not.toBeNull();
    expect((details as HTMLDetailsElement).open).toBe(false);
    // 默认可见的行 = 卡片直接子节点里的行,不含 details 内部的
    const visibleRows = [...container.querySelectorAll('[class*="row"]:not([class*="rowHead"])')]
      .filter((r) => !details!.contains(r));
    expect(visibleRows.length).toBe(4);
    expect(details!.querySelectorAll('[class*="row"]:not([class*="rowHead"])').length).toBe(2);
    expect(details!.querySelector("summary")?.textContent).toContain("2 项");
  });

  it("差距小的项排在后面——默认可见的是差距最大的那几行", () => {
    const metrics = [m("small", 51, 50), m("huge", 95, 5), m("mid", 70, 40)];
    const { container } = render(
      <PercentileGroupSection
        title="进攻百分位" windowNote="窗口说明" homeName="伯恩茅斯" awayName="布伦特福德"
        group={group({ metrics })}
      />,
    );
    const details = container.querySelector("details");
    const visibleLabels = [...container.querySelectorAll('[class*="label"]')]
      .filter((l) => !details || !details.contains(l))
      .map((l) => l.textContent);
    expect(visibleLabels[0]).toBe("指标huge");
    expect(visibleLabels).not.toContain("指标small");
  });

  it("轴刻度「垫底/联赛中游/第一」每组只标一次,不是每行都标", () => {
    const metrics = [m("a", 95, 5), m("b", 90, 10)];
    const { container } = render(
      <PercentileGroupSection
        title="进攻百分位" windowNote="窗口说明" homeName="伯恩茅斯" awayName="布伦特福德"
        group={group({ metrics })}
      />,
    );
    expect(container.querySelectorAll('[class*="axisLabels"]').length).toBe(1);
  });

  it("「联赛样本 N 队」在卡片头出现一次,不是每行一次", () => {
    const metrics = [m("a", 95, 5), m("b", 90, 10)];
    const { container } = render(
      <PercentileGroupSection
        title="进攻百分位" windowNote="窗口说明" homeName="伯恩茅斯" awayName="布伦特福德"
        group={group({ metrics })}
      />,
    );
    expect(container.querySelectorAll('[class*="sampleNote"]').length).toBe(1);
  });

  it("跨联赛模式:百分位轴/分位小注/联赛样本行全部不渲染,但差值胶囊还在", () => {
    // 欧战比赛没有共同的参照人群,后端把两侧百分位置 null。画一根空轴、
    // 每行印一遍「暂无分位」都只是噪声——但站长要的「多了多少」必须留着。
    const rawMetric = metric({
      unit: "球/场", home_value: 1.69, away_value: 1.22,
      home_percentile: null, away_percentile: null, league_sample_size: 0,
    });
    const g = group({
      metrics: [rawMetric], home_group_percentile: null, away_group_percentile: null,
    });
    const { container } = render(
      <PercentileGroupSection
        title="进攻数据" windowNote="窗口说明" homeName="维京" awayName="拜仁"
        group={g} mode="cross_league_raw" showMethodNote
      />,
    );

    expect(container.querySelector('[class*="axis"]')).toBeNull();
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.queryByText(/第 \d+ 分位/)).toBeNull();
    expect(screen.queryByText("暂无分位")).toBeNull();
    expect(container.querySelector('[class*="sampleNote"]')).toBeNull();
    expect(screen.queryByText("联赛中游")).toBeNull();

    const chip = container.querySelector('[class*="deltaChip"]');
    expect(chip?.textContent).toContain("维京");
    expect(chip?.textContent).toContain("↑0.47球/场");
    // 数值本身照常展示
    expect(screen.getByText("1.69球/场")).toBeTruthy();
  });

  it("跨联赛模式不给领先方高亮——那层队色加粗读作「这队更好」", () => {
    const g = group({
      metrics: [metric({
        home_value: 1.69, away_value: 1.22,
        home_percentile: null, away_percentile: null, league_sample_size: 0,
      })],
      home_group_percentile: null, away_group_percentile: null,
    });
    const { container } = render(
      <PercentileGroupSection
        title="进攻数据" windowNote="窗口说明" homeName="维京" awayName="拜仁"
        group={g} mode="cross_league_raw"
      />,
    );
    const sides = [...container.querySelectorAll('[class*="side"][data-side]')];
    expect(sides.length).toBe(2);
    expect(sides.every((s) => s.getAttribute("data-leader") === null)).toBe(true);
  });

  it("跨联赛模式全卡片不出现强弱措辞,且不重复印免责说明", () => {
    const g = group({
      metrics: [metric({
        key: "xga", name_zh: "让出预期进球(xGA)", direction: "lower_better",
        home_value: 1.61, away_value: 1.21,
        home_percentile: null, away_percentile: null, league_sample_size: 0,
      })],
      home_group_percentile: null, away_group_percentile: null,
    });
    const { container } = render(
      <PercentileGroupSection
        title="防守数据" windowNote="窗口说明" homeName="维京" awayName="拜仁"
        group={g} mode="cross_league_raw" showMethodNote
      />,
    );
    expect(container.textContent).not.toMatch(/更强|更弱|占优势/);
    // 免责只在总览卡片说一次(scope_note),这里整行不出——三段各印一遍
    // 在手机首屏就是三次重复(2026-09-10 站长复看)。
    expect(container.querySelector('[class*="verdict"]')).toBeNull();
    expect(container.textContent).not.toContain("不比强弱");
  });

  it("联赛模式完全不受影响:轴、分位、样本行照旧", () => {
    const { container } = render(
      <PercentileGroupSection
        title="进攻百分位" windowNote="窗口说明" homeName="伯恩茅斯" awayName="布伦特福德" group={group()} />,
    );
    expect(container.querySelector('[class*="axis"]')).not.toBeNull();
    expect(screen.getByText("第 87 分位")).toBeTruthy();
    expect(container.querySelector('[class*="sampleNote"]')).not.toBeNull();
  });

  it("口径说明只在被显式要求时渲染(全页只出现一次)", () => {
    const g = group();
    const { container: without } = render(
      <PercentileGroupSection title="进攻百分位" windowNote="窗口说明" homeName="A" awayName="B" group={g} />,
    );
    expect(without.querySelector('[class*="methodSummary"]')).toBeNull();
    cleanup();
    const { container: withNote } = render(
      <PercentileGroupSection title="控球百分位" windowNote="窗口说明" homeName="A" awayName="B" group={g} showMethodNote />,
    );
    expect(withNote.querySelector('[class*="methodSummary"]')).not.toBeNull();
  });
});
