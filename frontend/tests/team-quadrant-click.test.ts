/**
 * 队徽象限图的点击命中几何(2026-09-09)。
 *
 * headless SSR 模式下 zrender 没有 DOM 事件代理,`chart.on('click')` 不会被
 * `handler.dispatch` 触发(实测),但 `handler.findHover(x, y)` 的命中测试是真的——
 * 它就是浏览器里一次点击第一步要做的事。布局是纯函数,我们精确知道每个队徽画在哪
 * (真实坐标 + 避让偏移),所以能断言:在每个队徽被画出来的位置都能命中一个图形元素
 * (含无队徽的兜底圆点),绘图区空白处命不中。点击 → 选中的完整链路由
 * resolveClickedKey 的单元测试 + Playwright 浏览器验证覆盖。
 */

import * as echarts from "echarts";
import { describe, expect, it } from "vitest";
import { buildQuadrantOption } from "@/components/league/quadrantOption";
import { VIEWS, collectPoints, mean } from "@/components/league/quadrantViews";
import {
  QUADRANT_GRID,
  crestSizeFor,
  layoutCrests,
  niceAxisRange,
} from "@/components/charts/crestQuadrantLayout";
import type { ChartColors } from "@/components/charts/useChartColors";
import type { TeamSeasonStatRow } from "@/lib/api-v1";

const COLORS: ChartColors = {
  teal: "#087e78", navy: "#1d6f8b", win: "#287851", loss: "#b83b2d", draw: "#706c64",
  ink: "#0d2c3d", ink2: "#40535d", ink3: "#5a6b73", grey: "#b8c6c6", surface: "#ffffff",
  isDark: false, pitchBg: "#f8fafa",
};

function teamRow(i: number, crest: boolean): TeamSeasonStatRow {
  return {
    team: {
      team_id: 100 + i,
      name: `球队${i}`,
      name_en: null,
      crest_url: crest ? `data:image/png;base64,iVBORw0KGgo=#${i}` : null,
    },
    matches_played: 3,
    avg_expected_goals: 1.0 + ((i * 7) % 10) / 10,
    avg_expected_goals_conceded: 0.9 + ((i * 3) % 8) / 10,
  } as TeamSeasonStatRow;
}

describe("队徽点击命中几何(findHover)", () => {
  const SIZE = { width: 360, height: 380 };
  const view = VIEWS[0];
  const rows = Array.from({ length: 20 }, (_, i) => teamRow(i, i % 6 !== 0));
  const pts = collectPoints(rows, view);
  const mx = mean(pts.map((p) => p.x));
  const my = mean(pts.map((p) => p.y));
  const xr = niceAxisRange(pts.map((p) => p.x), { pad: 0.14 });
  const yr = niceAxisRange(pts.map((p) => p.y), { pad: 0.16 });
  const box = { ...SIZE, grid: QUADRANT_GRID };
  const crestSize = crestSizeFor(box, pts.length);
  const layout = layoutCrests({ pts, box, xr, yr, yInverse: true, radius: crestSize / 2 + 4 });

  function withChart<T>(fn: (chart: echarts.ECharts) => T): T {
    const chart = echarts.init(null, null, { renderer: "svg", ssr: true, ...SIZE });
    try {
      chart.setOption(
        buildQuadrantOption({
          view, pts, mx, my, colors: COLORS, labelled: new Set(), crestSize, layout, xr, yr,
          grid: QUADRANT_GRID, selectedIndexes: [],
        }),
      );
      return fn(chart);
    } finally {
      chart.dispose();
    }
  }

  it("每个队徽被画出来的位置(真实坐标 + 偏移)都命中图形元素,含无队徽兜底圆点", () => {
    const misses = withChart((chart) => {
      const handler = chart.getZr().handler as unknown as {
        findHover: (x: number, y: number) => { target?: unknown };
      };
      return pts
        .map((p, i) => {
          const [dx, dy] = layout.offset[i];
          const hit = handler.findHover(layout.base[i].px + dx, layout.base[i].py + dy).target;
          return hit ? null : `${p.name}${p.crestUrl ? "" : "(无队徽)"}`;
        })
        .filter((m): m is string => m != null);
    });
    expect(misses).toEqual([]);
  });

  it("绘图区角落(远离所有队徽)命不中任何图形——没有引线/圆点补位", () => {
    // 2026-09-09:曾经在避让位移较大的点的真实坐标画一个小圆点兜底可点击,
    // 用户反馈"坐标点该全部是队徽,不该有点"后已移除(quadrantOption.ts 头部
    // 说明)——现在真实坐标一旦不再是徽章绘制处,那里就是真的空白,不再假装
    // 可点。
    withChart((chart) => {
      const handler = chart.getZr().handler as unknown as {
        findHover: (x: number, y: number) => { target?: unknown };
      };
      expect(handler.findHover(QUADRANT_GRID.left + 2, QUADRANT_GRID.top + 2).target).toBeFalsy();
    });
  });
});
