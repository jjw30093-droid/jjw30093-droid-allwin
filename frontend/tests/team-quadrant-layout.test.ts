/**
 * 队徽象限图布局数学(2026-09-09,CLAUDE.md §11.3)。
 *
 * 整套"确定性坐标轴 + 纯 JS 避让"方案压在一个假设上:显式 min/max +
 * 固定像素 grid 时,我们算的像素位置与 ECharts 实际画的位置逐像素一致。
 * 第一组用例用项目自带 echarts headless SSR 真渲染一次,从 SVG 的
 * transform 里读回符号位置与 toPixel 比对——ECharts 升级若改了 grid 语义,
 * 这条会先炸,而不是线上徽章整体偏移。
 */

import * as echarts from "echarts";
import { describe, expect, it } from "vitest";
import {
  CREST,
  QUADRANT_GRID,
  crestSizeFor,
  crestSymbol,
  estimateLabelWidth,
  hitSizeFor,
  layoutCrests,
  niceAxisRange,
  resolveLabelVisibility,
  toPixel,
  type LabelCandidate,
  type PlotBox,
} from "@/components/charts/crestQuadrantLayout";

function renderedSymbolPositions(opts: {
  width: number;
  height: number;
  xr: { min: number; max: number; interval: number };
  yr: { min: number; max: number; interval: number };
  inverse: boolean;
  pts: [number, number][];
}): { px: number; py: number }[] {
  const chart = echarts.init(null, null, {
    renderer: "svg",
    ssr: true,
    width: opts.width,
    height: opts.height,
  });
  try {
    chart.setOption({
      grid: QUADRANT_GRID,
      xAxis: { type: "value", ...opts.xr },
      yAxis: { type: "value", ...opts.yr, inverse: opts.inverse },
      series: [{ type: "scatter", symbol: "circle", symbolSize: 10, data: opts.pts }],
    });
    const svg = chart.renderToSVGString();
    const out: { px: number; py: number }[] = [];
    const re = /<path[^>]*transform="matrix\(([^)]+)\)"[^>]*ecmeta_ssr_type="chart"/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(svg))) {
      const parts = m[1].split(",").map(Number);
      out.push({ px: parts[4], py: parts[5] });
    }
    return out;
  } finally {
    chart.dispose();
  }
}

describe("toPixel 与 ECharts 实际渲染逐像素一致", () => {
  const xr = { min: 0, max: 10, interval: 2 };
  const yr = { min: 0, max: 5, interval: 1 };
  const pts: [number, number][] = [
    [5, 2.5],
    [0, 0],
    [10, 5],
    [1.5, 4.25],
  ];

  it.each([false, true])("inverse=%s", (inverse) => {
    const box: PlotBox = { width: 600, height: 400, grid: QUADRANT_GRID };
    const rendered = renderedSymbolPositions({ width: 600, height: 400, xr, yr, inverse, pts });
    expect(rendered).toHaveLength(pts.length);
    pts.forEach(([x, y], i) => {
      const mine = toPixel({ x, y }, box, xr, yr, inverse);
      expect(Math.abs(mine.px - rendered[i].px)).toBeLessThanOrEqual(0.5);
      expect(Math.abs(mine.py - rendered[i].py)).toBeLessThanOrEqual(0.5);
    });
  });
});

describe("niceAxisRange", () => {
  it("原始端点值 0.47236656243863856 不会变成刻度标签(每个刻度都是干净小数)", () => {
    const r = niceAxisRange([0.47236656243863856, 1.83, 1.2, 0.9]);
    const decimals = (v: number) => (v.toString().split(".")[1] ?? "").length;
    expect(decimals(r.min)).toBeLessThanOrEqual(2);
    expect(decimals(r.max)).toBeLessThanOrEqual(2);
    expect(decimals(r.interval)).toBeLessThanOrEqual(2);
    for (let v = r.min; v <= r.max + 1e-9; v += r.interval) {
      expect(decimals(Number(v.toFixed(6)))).toBeLessThanOrEqual(2);
    }
  });

  it("端点覆盖全部数据并留白(不让点贴在边框上)", () => {
    const vals = [10.2, 15.7, 12.1];
    const r = niceAxisRange(vals);
    expect(r.min).toBeLessThan(Math.min(...vals));
    expect(r.max).toBeGreaterThan(Math.max(...vals));
  });

  it("全等输入不产生零宽区间", () => {
    const r = niceAxisRange([1.4, 1.4, 1.4]);
    expect(r.max).toBeGreaterThan(r.min);
    expect(r.interval).toBeGreaterThan(0);
  });

  it("负值与空输入都不抛", () => {
    expect(() => niceAxisRange([-3, -1, -2])).not.toThrow();
    const r = niceAxisRange([]);
    expect(r.max).toBeGreaterThan(r.min);
  });
});

describe("layoutCrests 避让", () => {
  const box: PlotBox = { width: 360, height: 380, grid: QUADRANT_GRID };
  const xr = { min: 0, max: 3, interval: 0.5 };
  const yr = { min: 0, max: 3, interval: 0.5 };
  // 20 队挤在均值附近的真实形状(赛季初每队 3 场时就是这样)
  const crowded = Array.from({ length: 20 }, (_, i) => ({
    x: 1.4 + ((i * 7) % 5) * 0.04,
    y: 1.3 + ((i * 3) % 4) * 0.05,
  }));
  const radius = 16;

  it("确定性:同输入两次输出深相等", () => {
    const a = layoutCrests({ pts: crowded, box, xr, yr, yInverse: true, radius });
    const b = layoutCrests({ pts: crowded, box, xr, yr, yInverse: true, radius });
    expect(a).toEqual(b);
  });

  it("从不修改入参", () => {
    const snapshot = JSON.stringify(crowded);
    layoutCrests({ pts: crowded, box, xr, yr, yInverse: false, radius });
    expect(JSON.stringify(crowded)).toBe(snapshot);
  });

  it("最小间距严格改善,且位移不超过上限", () => {
    const layout = layoutCrests({ pts: crowded, box, xr, yr, yInverse: false, radius });
    const before = (() => {
      let m = Infinity;
      for (let j = 0; j < layout.base.length; j++)
        for (let k = j + 1; k < layout.base.length; k++)
          m = Math.min(
            m,
            Math.hypot(layout.base[k].px - layout.base[j].px, layout.base[k].py - layout.base[j].py),
          );
      return m;
    })();
    expect(layout.minSpacing).toBeGreaterThan(before);
    const maxShift = CREST.MAX_SHIFT_RATIO * radius * 2;
    for (const [dx, dy] of layout.offset) {
      expect(Math.hypot(dx, dy)).toBeLessThanOrEqual(maxShift + 0.5);
    }
  });

  it("避让后的位置都在绘图区内(不盖住轴标签)", () => {
    const layout = layoutCrests({ pts: crowded, box, xr, yr, yInverse: false, radius });
    layout.base.forEach((b, i) => {
      const px = b.px + layout.offset[i][0];
      const py = b.py + layout.offset[i][1];
      expect(px).toBeGreaterThanOrEqual(QUADRANT_GRID.left + radius - 0.5);
      expect(px).toBeLessThanOrEqual(box.width - QUADRANT_GRID.right - radius + 0.5);
      expect(py).toBeGreaterThanOrEqual(QUADRANT_GRID.top + radius - 0.5);
      expect(py).toBeLessThanOrEqual(box.height - QUADRANT_GRID.bottom - radius + 0.5);
    });
  });

  it("完全重合的点被分开,输出有限无 NaN", () => {
    const same = [
      { x: 1.5, y: 1.5 },
      { x: 1.5, y: 1.5 },
      { x: 1.5, y: 1.5 },
    ];
    const layout = layoutCrests({ pts: same, box, xr, yr, yInverse: false, radius });
    for (const [dx, dy] of layout.offset) {
      expect(Number.isFinite(dx)).toBe(true);
      expect(Number.isFinite(dy)).toBe(true);
    }
    expect(layout.minSpacing).toBeGreaterThan(0);
  });

  it("leader 标志与位移阈值一致", () => {
    const layout = layoutCrests({ pts: crowded, box, xr, yr, yInverse: false, radius });
    layout.offset.forEach(([dx, dy], i) => {
      expect(layout.leader[i]).toBe(Math.hypot(dx, dy) > CREST.LEADER_MIN_SHIFT);
    });
  });

  it("互不重叠的点零偏移、无引线", () => {
    const spread = [
      { x: 0.5, y: 0.5 },
      { x: 2.5, y: 0.5 },
      { x: 0.5, y: 2.5 },
      { x: 2.5, y: 2.5 },
    ];
    const layout = layoutCrests({ pts: spread, box, xr, yr, yInverse: false, radius });
    expect(layout.offset.every(([dx, dy]) => dx === 0 && dy === 0)).toBe(true);
    expect(layout.leader.every((l) => !l)).toBe(true);
  });

  it("空输入与单点都安全", () => {
    expect(() => layoutCrests({ pts: [], box, xr, yr, yInverse: false, radius })).not.toThrow();
    const one = layoutCrests({ pts: [{ x: 1, y: 1 }], box, xr, yr, yInverse: false, radius });
    expect(one.minSpacing).toBe(Infinity);
    expect(hitSizeFor(one, 24)).toBe(CREST.HIT_MAX);
  });
});

describe("crestSizeFor / hitSizeFor / crestSymbol", () => {
  it("360px 卡片 20 队落在 [MIN, MAX] 之内且小于 1080px 桌面的尺寸", () => {
    const narrow = crestSizeFor({ width: 360, height: 380, grid: QUADRANT_GRID }, 20);
    const wide = crestSizeFor({ width: 1080, height: 380, grid: QUADRANT_GRID }, 20);
    expect(narrow).toBeGreaterThanOrEqual(CREST.MIN);
    expect(narrow).toBeLessThanOrEqual(CREST.MAX);
    expect(wide).toBeGreaterThanOrEqual(narrow);
    expect(wide).toBeLessThanOrEqual(CREST.MAX);
  });

  it("命中层直径 = clamp(实际间距, 徽章边长, 44)", () => {
    const fake = { base: [], offset: [], leader: [], minSpacing: 30 };
    expect(hitSizeFor(fake, 24)).toBe(30);
    expect(hitSizeFor({ ...fake, minSpacing: 100 }, 24)).toBe(CREST.HIT_MAX);
    expect(hitSizeFor({ ...fake, minSpacing: 10 }, 24)).toBe(24);
    expect(hitSizeFor(null, 24)).toBe(CREST.HIT_MAX);
  });

  it("有 URL 用 image://,无 URL 退回 circle", () => {
    expect(crestSymbol("/api/v1/media/team-crests/fotmob/1.png?v=abc")).toBe(
      "image:///api/v1/media/team-crests/fotmob/1.png?v=abc",
    );
    expect(crestSymbol(null)).toBe("circle");
    expect(crestSymbol(undefined)).toBe("circle");
  });
});

describe("estimateLabelWidth / resolveLabelVisibility(2026-09-09,队名压住队徽的修复)", () => {
  it("中文字符按整个 fontSize 记,越长的队名估算宽度越大", () => {
    const fs = 12;
    expect(estimateLabelWidth("阿森纳", fs)).toBeCloseTo(fs * 3);
    expect(estimateLabelWidth("曼彻斯特联", fs)).toBeGreaterThan(estimateLabelWidth("曼联", fs));
  });

  it("孤立的一个点:标签上方没有任何别的队徽,正常显示", () => {
    const solo: LabelCandidate[] = [{ cx: 100, cy: 100, radius: 15, text: "阿森纳" }];
    expect(resolveLabelVisibility(solo, { fontSize: 12 })).toEqual([true]);
  });

  it("core bug 复现:两个队徽紧挨着,上面那个的标签会压在下面那个头顶——必须摘掉", () => {
    // 复刻用户截图的密集区形状:同一列纵向排开,间距只比半径大一点点。
    const r = 15;
    const candidates: LabelCandidate[] = [
      { cx: 100, cy: 60, radius: r, text: "阿斯顿维拉" }, // 上面这个的标签会往上飘,不会压到别人
      { cx: 100, cy: 90, radius: r, text: "热刺" }, // 它的标签正好落在上面那支队徽的圆圈里
      { cx: 100, cy: 120, radius: r, text: "赫尔城" },
    ];
    const visible = resolveLabelVisibility(candidates, { fontSize: 12, distance: 5 });
    // 「热刺」的标签框(它上方 distance+height 的区域)与「阿斯顿维拉」的队徽圆相交 → 必须为 false
    expect(visible[1]).toBe(false);
    // 最上面一个头顶没有别的队徽,应该正常显示
    expect(visible[0]).toBe(true);
  });

  it("横向离得够远时互不影响,标签都能显示", () => {
    const r = 12;
    const candidates: LabelCandidate[] = [
      { cx: 50, cy: 100, radius: r, text: "曼城" },
      { cx: 250, cy: 100, radius: r, text: "利物浦" },
    ];
    expect(resolveLabelVisibility(candidates, { fontSize: 12 })).toEqual([true, true]);
  });

  it("text 为 null 的点不参与判定(视为不想显示),但仍会挡住别人的标签", () => {
    const r = 15;
    const candidates: LabelCandidate[] = [
      { cx: 100, cy: 60, radius: r, text: null }, // 中游球队,自己不想显示名字
      { cx: 100, cy: 90, radius: r, text: "热刺" }, // 但它的队徽仍然会挡住热刺头顶的标签
    ];
    const visible = resolveLabelVisibility(candidates, { fontSize: 12, distance: 5 });
    expect(visible[0]).toBe(false); // text=null 本身就不显示
    expect(visible[1]).toBe(false); // 被邻居的队徽挡住
  });

  it("选中态队徽会放大(CREST.SELECTED_SCALE),同样的间距原本躲得开也会被放大后的圆盖住", () => {
    // 精心选的间距:小半径够不着标签框、放大后(见 quadrantOption.ts 的
    // SELECTED_SCALE 用法)刚好伸进去——验证"选中会让邻居的标签更容易被摘掉"
    // 这个方向是对的,不绑定具体的 SELECTED_SCALE 数值(避免常量调整就改测试)。
    const base: LabelCandidate = { cx: 100, cy: 90, radius: 15, text: "热刺" };
    const neighborSmall: LabelCandidate = { cx: 100, cy: 44.2, radius: 8, text: null };
    const neighborBig: LabelCandidate = { cx: 100, cy: 44.2, radius: 10, text: null };
    expect(resolveLabelVisibility([neighborSmall, base], { fontSize: 12, distance: 5 })[1]).toBe(true);
    expect(resolveLabelVisibility([neighborBig, base], { fontSize: 12, distance: 5 })[1]).toBe(false);
  });

  it("空数组安全", () => {
    expect(resolveLabelVisibility([], { fontSize: 12 })).toEqual([]);
  });
});
