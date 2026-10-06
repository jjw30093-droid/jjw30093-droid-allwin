// @vitest-environment node
/**
 * xG 走势图进球人名的摆放(components/matches/xgRaceLabels.ts):
 * 2026-09-30 定位——原先主上客下固定摆放,手机尺寸下客队人名 82% 压在自家竖线上、93% 的场次至少有一处重叠。
 * 这里在 300 场模拟上核对新摆法的冲突率,并用 ECharts 真实渲染核对文字确实落在算好的位置、带描边、不压自家线。
 */
import * as echarts from "echarts";
import { describe, expect, it } from "vitest";
import { buildOption, cumulativeSeries, type XgRaceShot } from "@/components/matches/XgRaceChart";
import { niceYAxis, placeGoalLabels, stepSegments, textWidth, type PlotGeometry } from "@/components/matches/xgRaceLabels";
import { prepareMatch, simulateOnce } from "@/features/simulator/engine";
import { toReportShots } from "@/features/simulator/xg";
import { matchSetup, simParams } from "./fixtures/simulatorParams";

const C = { teal: "#087e78", navy: "#b5540b", win: "#287851", loss: "#b83b2d", draw: "#706c64", ink: "#0d2c3d", ink2: "#40535d", ink3: "#5a6b73", grey: "#b8c6c6", surface: "#ffffff", isDark: false, pitchBg: "#f8fafa" };
const W = 311;
const H = 220; // 模拟器结果页的图高
const NAMES = ["萨卡", "格瓦迪奥尔"];

/** 模拟比赛的射门;每队最多保留 2 个进球(测试夹具的进球偏多,截断后更接近真实比分),人名固定成最长的一类 */
function matches(n: number) {
  const r = prepareMatch(simParams(), matchSetup());
  if (!r.ok) throw new Error(r.error);
  return Array.from({ length: n }, (_, k) => {
    const s = simulateOnce(r.config, k + 1);
    const kept: Record<string, number> = { true: 0, false: 0 };
    const shots = toReportShots(s.events, [10, 20], s.halfTimeTick).map((x) => {
      const goal = x.outcome === "Goal" && ++kept[String(x.is_home)] <= 2;
      return { ...x, outcome: goal ? "Goal" : "Miss", player_name: goal ? NAMES[x.is_home ? 0 : 1] : x.player_name } as XgRaceShot;
    });
    const st = { firstHalf: s.stoppage[0], secondHalf: s.stoppage[1] };
    return { shots, st, end: 90 + st.firstHalf + st.secondHalf };
  });
}

describe("进球人名摆放(纯函数)", () => {
  // 2026-09-30 实测(783 个人名):压自家线 6(0.8%)、压对方线 16(2.0%)、互相重叠 4(0.5%)、出界 0;原摆法依次是 61% / 36% / 18% / 11%。
  // 剩下压自家线的 6 个全是开场前 8 分钟、贴着左下角的长名字(如"格瓦迪奥尔",60px),后面紧跟几脚射门,四周都没有空位;靠描边保证可读。
  it("300 场模拟:压自家线 ≤ 1%、压对方线 ≤ 4%、互相重叠 ≤ 2%、不出界", () => {
    let n = 0;
    const c = { own: 0, other: 0, label: 0, outside: 0 };
    let maxFirstGoalYOffset = 0;
    for (const { shots, st, end } of matches(300)) {
      const lines: [ReturnType<typeof cumulativeSeries>, ReturnType<typeof cumulativeSeries>] = [cumulativeSeries(shots, true, st), cumulativeSeries(shots, false, st)];
      const ext = lines.map((l) => [...l, { ...l[l.length - 1], minute: end, goal: null }]) as typeof lines;
      const plotH = H - 34 - 28;
      const y = niceYAxis(Math.max(...ext[0].map((p) => p.total), ...ext[1].map((p) => p.total)), plotH, 12);
      const g: PlotGeometry = { w: W - 46 - 18, h: plotH, xMax: end, yMax: y.max, font: 12, r: 5 };
      const goals = ext.flatMap((pts, team) => pts.filter((p) => p.goal).map((p) => ({ team: team as 0 | 1, minute: p.minute, total: p.total, name: p.goal!.player_name ?? "" })));
      const placements = placeGoalLabels(goals, ext, g);
      if (goals.length) {
        const first = goals.reduce((best, goal, i) => (goal.minute < goals[best].minute ? i : best), 0);
        maxFirstGoalYOffset = Math.max(maxFirstGoalYOffset, Math.abs(placements[first].oy));
      }
      for (const p of placements) {
        n += 1;
        c.own += +p.conflicts.ownLine;
        c.other += +p.conflicts.otherLine;
        c.label += +p.conflicts.label;
        c.outside += +p.conflicts.outside;
      }
    }
    expect(n).toBeGreaterThan(500);
    expect(c.own / n).toBeLessThanOrEqual(0.01);
    expect(c.outside).toBe(0);
    expect(c.other / n).toBeLessThanOrEqual(0.04);
    expect(c.label / n).toBeLessThanOrEqual(0.02);
    expect(maxFirstGoalYOffset).toBeLessThanOrEqual(30);
  });

  it("左上优先;左上被占时改右下", () => {
    const g: PlotGeometry = { w: 247, h: 158, xMax: 96, yMax: 1.8, font: 12, r: 5 };
    const lines: [{ minute: number; total: number }[], { minute: number; total: number }[]] = [
      [{ minute: 0, total: 0 }, { minute: 20, total: 0.4 }, { minute: 96, total: 0.4 }],
      [{ minute: 0, total: 0 }, { minute: 22, total: 0.35 }, { minute: 96, total: 0.35 }],
    ];
    const [a, b] = placeGoalLabels(
      [{ team: 0, minute: 20, total: 0.4, name: "萨卡" }, { team: 1, minute: 22, total: 0.35, name: "格瓦迪奥尔" }],
      lines,
      g,
    );
    expect(a.side).toBe("UL");
    expect(b.side).toBe("LR"); // 左上会压到萨卡的人名
  });

  it("靠前进球的人名留在纵轴右侧", () => {
    const g: PlotGeometry = { w: 247, h: 158, xMax: 96, yMax: 1.8, font: 12, r: 5 };
    const goal = { team: 0 as const, minute: 12, total: 0.24, name: "哈弗茨" };
    const lines: [{ minute: number; total: number }[], { minute: number; total: number }[]] = [
      [{ minute: 0, total: 0 }, goal, { minute: 96, total: 0.24 }],
      [{ minute: 0, total: 0 }, { minute: 96, total: 0 }],
    ];
    const p = placeGoalLabels([goal], lines, g)[0];
    const pointX = (goal.minute / g.xMax) * g.w;
    const labelLeft = p.side === "UL" || p.side === "LL"
      ? pointX + p.ox - textWidth(goal.name, g.font)
      : pointX + p.ox;
    expect(labelLeft).toBeGreaterThanOrEqual(8);
  });

  it("阶梯线线段:先水平到下一点再竖直跳升", () => {
    const g: PlotGeometry = { w: 100, h: 100, xMax: 100, yMax: 1, font: 12, r: 5 };
    expect(stepSegments([{ minute: 0, total: 0 }, { minute: 50, total: 0.5 }], g)).toEqual([
      [0, 100, 50, 100],
      [50, 100, 50, 50],
    ]);
  });

  it("纵轴:最高点上方留出人名空间,刻度不超过 6 格且整除", () => {
    for (const m of [0.05, 0.4, 0.97, 1.6, 2.35, 4.1]) {
      const { max, interval } = niceYAxis(m, 158, 12);
      expect(max).toBeGreaterThanOrEqual(m * (1 + 16 / 158) - 1e-9);
      expect(max / interval).toBeLessThanOrEqual(6);
      expect(Math.abs(max / interval - Math.round(max / interval))).toBeLessThan(1e-6);
      // 每个刻度保留 1 位小数后不失真(0.25 这类间隔会显示成 0.3)
      for (let v = 0; v <= max + 1e-9; v += interval) expect(Math.abs(+v.toFixed(1) - v)).toBeLessThan(1e-6);
    }
  });

  it("文字宽度:中文按一个字号,ASCII 按 0.6", () => {
    expect(textWidth("萨卡", 12)).toBe(24);
    expect(textWidth("B费", 10)).toBe(16);
  });
});

describe("ECharts 真实渲染", () => {
  it("人名落在算好的锚点、带与底色同色的描边;压自家线的不超过 2%", () => {
    let checked = 0;
    let ownHits = 0;
    for (const { shots, st, end } of matches(40)) {
      const opt = { ...buildOption(cumulativeSeries(shots, true, st), cumulativeSeries(shots, false, st), "阿森纳", "曼城", end, "interactive", C as never, st, { width: W, height: H }), animation: false };
      const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: W, height: H });
      chart.setOption(opt);
      const svg = chart.renderToSVGString();
      // 自家线段(像素)
      const segs = (color: string) =>
        [...svg.matchAll(new RegExp(`<path d="(M[^"]*)"[^>]*stroke="${color}"`, "g"))].flatMap((m) => {
          const pts = [...m[1].matchAll(/[ML]([\d.-]+) ([\d.-]+)/g)].map((x) => [+x[1], +x[2]]);
          return pts.slice(1).map((p, i) => [pts[i][0], pts[i][1], p[0], p[1]]);
        });
      const own = { [C.teal]: segs(C.teal), [C.navy]: segs(C.navy) };
      const series = opt.series as { markPoint: { data: { coord: [number, number]; name: string; label: { position: [number, number]; align: string } }[] } }[];
      for (const s of series) {
        for (const d of s.markPoint.data) {
          const [px, py] = chart.convertToPixel({ gridIndex: 0 }, d.coord) as number[];
          const text = [...svg.matchAll(/<text[^>]*text-anchor="(\w+)"[^>]*y="(-?[\d.]+)" transform="translate\(([\d.-]+) ([\d.-]+)\)" fill="(#[0-9a-f]+)" stroke="#ffffff" stroke-width="3"[^>]*>([^<]+)<\/text>/g)].find(
            (m) => m[6] === d.name && Math.abs(+m[3] - (px - 5 + d.label.position[0])) < 0.6 && Math.abs(+m[4] - (py - 5 + d.label.position[1])) < 0.6,
          );
          expect(text, `${d.name} 未落在算好的位置或缺少描边`).toBeTruthy();
          const [anchor, dy, tx, ty, fill] = [text![1], +text![2], +text![3], +text![4], text![5]];
          const w = [...d.name].length * 12;
          const x0 = anchor === "end" ? tx - w : anchor === "middle" ? tx - w / 2 : tx;
          const cy = ty + dy;
          const box = { x0: x0 + 0.5, x1: x0 + w - 0.5, y0: cy - 5.5, y1: cy + 5.5 };
          const hitsOwn = own[fill].some(([ax, ay, bx, by]) =>
            Math.abs(ax - bx) < 0.01
              ? ax > box.x0 && ax < box.x1 && Math.max(ay, by) > box.y0 && Math.min(ay, by) < box.y1
              : Math.abs(ay - by) < 0.01 && ay > box.y0 && ay < box.y1 && Math.max(ax, bx) > box.x0 && Math.min(ax, bx) < box.x1,
          );
          ownHits += +hitsOwn;
          checked += 1;
        }
      }
      chart.dispose();
    }
    expect(checked).toBeGreaterThan(60);
    expect(ownHits / checked).toBeLessThanOrEqual(0.02);
  });
});
