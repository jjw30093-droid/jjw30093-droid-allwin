/**
 * 射门标记真实点击路径(2026-08-25,方案 §1.3 的硬性要求)。
 *
 * custom 系列是否把 data 项上的 `.shot` 对象原样透传进 click 参数,是
 * resolveClickedShot 主路径的前提——方案明确要求"必须在实现时用一条真实
 * 点击测试确认,不要默认它一定成立"。这里用项目自带 echarts 在 jsdom 里
 * 真实 init(svg 渲染器 + 显式宽高)、真实 setOption,再通过 zrender 的
 * 事件派发(getZr().handler.dispatch)在标记的像素坐标上触发一次 click:
 * 命中测试、事件包装、参数组装全部走 ECharts 真实代码路径,不是 mock。
 */

import * as echarts from "echarts";
import { describe, expect, it } from "vitest";
import {
  buildOption,
  orderShotsForRender,
  resolveClickedShot,
} from "@/components/matches/ShotMapChart";
import type { ChartColors } from "@/components/charts/useChartColors";
import type { MatchReportResponse } from "@/lib/api-v1";

type MatchReport = Extract<MatchReportResponse, { available: true }>;
type Shot = MatchReport["shots"][number];

const COLORS: ChartColors = {
  teal: "#087e78",
  navy: "#1d6f8b",
  win: "#287851",
  loss: "#b83b2d",
  draw: "#706c64",
  ink: "#0d2c3d",
  ink2: "#40535d",
  ink3: "#5a6b73",
  grey: "#b8c6c6",
  surface: "#ffffff",
  isDark: false,
  pitchBg: "#f8fafa",
};

/** zrender Handler 的 click 只有在同一位置先 mousedown+mouseup 过才会派发
 * (Handler.js:_downEl/_downPoint 校验,与真实浏览器行为一致)——所以这里
 * 派发完整的按下→抬起→点击序列,不是只发一个 click。 */
function dispatchClick(chart: echarts.ECharts, px: number, py: number): void {
  const handler = (chart.getZr() as unknown as {
    handler: { dispatch: (type: string, event: { zrX: number; zrY: number }) => void };
  }).handler;
  handler.dispatch("mousedown", { zrX: px, zrY: py });
  handler.dispatch("mouseup", { zrX: px, zrY: py });
  handler.dispatch("click", { zrX: px, zrY: py });
}

function shot(overrides: Partial<Shot>): Shot {
  return {
    player_id: "p1",
    player_name: "球员",
    team_id: 1,
    is_home: true,
    minute: 10,
    period: "FirstHalf",
    x: 90,
    y: 34,
    xg: 0.3,
    xgot: null,
    situation: "RegularPlay",
    outcome: "Goal",
    shot_type: "RightFoot",
    is_blocked: null,
    is_on_target: null,
    is_own_goal: false,
    is_own_goal_inferred: false,
    ...overrides,
  } as Shot;
}

describe("射门标记真实点击(ECharts 真实事件派发,非 mock)", () => {
  it("点击标记像素坐标 → click 参数经 resolveClickedShot 解析出正确的 shot", () => {
    const target = shot({ player_id: "clicked", x: 90, y: 34, outcome: "Goal" });
    const other = shot({ player_id: "other", is_home: false, x: 80, y: 20, outcome: "Miss" });
    const plotted = [target, other];
    const ordered = orderShotsForRender(plotted);

    const el = document.createElement("div");
    document.body.appendChild(el);
    const chart = echarts.init(el, null, { renderer: "svg", width: 420, height: 272 });
    try {
      chart.setOption(buildOption(plotted, "主队", "客队", COLORS));

      const received: unknown[] = [];
      chart.on("click", (params) => {
        received.push(params);
      });

      // 主队进球不镜像:数据坐标 (90, 34) → 像素坐标
      const [px, py] = chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [90, 34]) as [
        number,
        number,
      ];
      // zrender 真实命中测试 + 事件包装
      dispatchClick(chart, px, py);

      expect(received.length).toBeGreaterThan(0);
      const resolved = resolveClickedShot(received[0], ordered);
      expect(resolved).not.toBeNull();
      expect(resolved!.player_id).toBe("clicked");

      // dataIndex 兜底路径:剥掉 data 字段后仍能靠 seriesName+dataIndex 解析
      const raw = received[0] as { seriesName?: string; dataIndex?: number };
      const viaIndex = resolveClickedShot(
        { seriesName: raw.seriesName, dataIndex: raw.dataIndex },
        ordered,
      );
      expect(viaIndex).not.toBeNull();
      expect(viaIndex!.player_id).toBe("clicked");
    } finally {
      chart.dispose();
      el.remove();
    }
  });

  it("点击球场空白处 → 无 click 事件,resolveClickedShot 不产生选中", () => {
    const plotted = [shot({ player_id: "a", x: 100, y: 60, outcome: "Goal" })];

    const el = document.createElement("div");
    document.body.appendChild(el);
    const chart = echarts.init(el, null, { renderer: "svg", width: 420, height: 272 });
    try {
      chart.setOption(buildOption(plotted, "主队", "客队", COLORS));
      const received: unknown[] = [];
      chart.on("click", (params) => {
        received.push(params);
      });

      // 中圈附近没有任何标记
      const [px, py] = chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [52.5, 34]) as [
        number,
        number,
      ];
      dispatchClick(chart, px, py);

      expect(received).toHaveLength(0);
    } finally {
      chart.dispose();
      el.remove();
    }
  });
});
