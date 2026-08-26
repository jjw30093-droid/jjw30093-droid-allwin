/**
 * 射门标记真实点击路径(2026-08-25 起,2026-08-26 补充引用相等断言)。
 *
 * 这里用项目自带 echarts 真实 init(svg 渲染器 + 显式宽高)、真实
 * setOption,再通过 zrender 的事件派发(getZr().handler.dispatch)在标记
 * 的像素坐标上触发一次 click:命中测试、事件包装、参数组装全部走 ECharts
 * 真实代码路径,不是 mock。
 *
 * 2026-08-26 真实生产事故教训:本文件最初的断言只检查
 * `resolved!.player_id === "clicked"`(字段级相等),从未检查过
 * `resolved === target`(引用相等)——而 ECharts 的 custom 系列在
 * `setOption` 内部处理 `data` 时,会把 `data[i]` 里嵌套的非原始值(这里是
 * `shot` 对象)克隆一份;`params.data.shot` 拿到的是结构相同但引用不同的
 * 副本(`received[0].data.shot === target` 实测为 false,即使在这份"真实
 * 点击测试"、jsdom+svg 环境下也如此——不是浏览器 canvas 渲染特有的差异,
 * 是 ECharts 本身的行为)。字段级相等测试对此完全没有发现能力,而这正是
 * `resolveSelectedShot()` 下游用 `plotted.includes(selected)` 做引用相等
 * 判断时会失败、导致详情面板完全不出现的根本原因(线上复现:点击任意射门
 * 标记无任何反应)。现在 `resolveClickedShot()` 已改为优先信任 `dataIndex`
 * (直接从 `ordered` 数组按下标取,不经过这条克隆路径),下面显式补上引用
 * 相等断言,把"这条真实点击链路返回的确实是 plotted 里的原始对象"钉死。
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

      // 2026-08-26:ECharts 真实会克隆 data 项里嵌套的 shot 对象——这条
      // 断言直接证实那次事故的根因,不是靠推测。
      const rawShot = (received[0] as { data?: { shot?: Shot } }).data?.shot;
      expect(rawShot).not.toBe(target); // 克隆副本,不是原始引用
      expect(rawShot?.player_id).toBe("clicked"); // 但结构/字段确实一致

      const resolved = resolveClickedShot(received[0], ordered);
      expect(resolved).not.toBeNull();
      expect(resolved!.player_id).toBe("clicked");
      // 关键断言(2026-08-26 事故修复前这里会失败):必须是 plotted 里的
      // 真实引用,不是上面那份克隆副本——resolveSelectedShot 下游依赖的
      // 正是这个引用相等性。
      expect(resolved).toBe(target);

      // dataIndex 兜底路径:剥掉 data 字段后仍能靠 seriesName+dataIndex 解析
      const raw = received[0] as { seriesName?: string; dataIndex?: number };
      const viaIndex = resolveClickedShot(
        { seriesName: raw.seriesName, dataIndex: raw.dataIndex },
        ordered,
      );
      expect(viaIndex).not.toBeNull();
      expect(viaIndex!.player_id).toBe("clicked");
      expect(viaIndex).toBe(target);
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
