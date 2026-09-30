// @vitest-environment node
/** 动画时间线:单调、进球停留、半场停顿、同分钟射门错开、球的轨迹按结果收尾。 */
import { describe, expect, it } from "vitest";
import { prepareMatch, simulateOnce } from "@/features/simulator/engine";
import { ballAt, buildPlan, FLIGHT_MS, GOAL_HOLD_MS, HALF_TIME_HOLD_MS, MS_PER_TICK, resolvedOrder, SHOT_STAGGER_MS, stageSize, tickAt, toStage } from "@/features/simulator/playback";
import { RESULT_BLOCKED, RESULT_GOAL, RESULT_MISS, RESULT_SAVED } from "@/features/simulator/shotDetail";
import { matchSetup, simParams } from "./fixtures/simulatorParams";

function single(seed: number) {
  const r = prepareMatch(simParams(), matchSetup());
  if (!r.ok) throw new Error(r.error);
  return simulateOnce(r.config, seed);
}

describe("时间线", () => {
  it("分钟起点单调递增;1 倍速总时长在 25–60 秒", () => {
    for (const seed of [1, 7, 4242, 20260930]) {
      const s = single(seed);
      const p = buildPlan(s);
      for (let t = 1; t < p.tickStart.length; t++) expect(p.tickStart[t]).toBeGreaterThan(p.tickStart[t - 1]);
      expect(p.total).toBeGreaterThan(25_000);
      expect(p.total).toBeLessThan(60_000);
      expect(tickAt(p, 0)).toBe(0);
      expect(tickAt(p, p.fullTimeAt)).toBe(s.totalTicks);
      expect(tickAt(p, p.tickStart[30] + 1)).toBe(30);
    }
  });

  it("半场停顿:下半场第一分钟前空出停顿时间", () => {
    const s = single(7);
    const p = buildPlan(s);
    expect(p.halfTimeAt).not.toBeNull();
    expect(p.tickStart[s.halfTimeTick] - p.halfTimeAt!).toBe(HALF_TIME_HOLD_MS);
  });

  it("进球后停留:下一分钟在进球落定 + 停留之后才开始", () => {
    const s = single(1); // 4-2
    const p = buildPlan(s);
    s.events.forEach((e, i) => {
      if (e.kind !== "goal") return;
      expect(p.eventResolved[i]).toBe(p.eventStart[i] + FLIGHT_MS);
      const next = p.tickStart[e.tick + 1];
      if (next !== undefined && e.tick + 1 !== s.halfTimeTick) expect(next - p.eventResolved[i]).toBeGreaterThanOrEqual(GOAL_HOLD_MS);
    });
  });

  it("同一分钟里的多脚射门依次错开", () => {
    const s = { totalTicks: 3, halfTimeTick: 2, events: [0, 1, 2].map(() => ({ tick: 1, minute: 2, added: 0, clock: "2'", team: 0 as const, kind: "shot" as const, xg: 0.1 })) };
    const p = buildPlan(s);
    expect(p.eventStart).toEqual([MS_PER_TICK, MS_PER_TICK + SHOT_STAGGER_MS, MS_PER_TICK + 2 * SHOT_STAGGER_MS]);
    expect(resolvedOrder(p)).toEqual([0, 1, 2]);
  });
});

describe("球的轨迹", () => {
  const from: [number, number] = [90, 30];
  it("起点在射门点,飞行结束到达终点", () => {
    const end = { kind: "line" as const, x: 105, y: 35, z: 1, real: true };
    expect(ballAt(from, end, RESULT_GOAL, 0)).toMatchObject({ x: 90, y: 30 });
    const b = ballAt(from, end, RESULT_GOAL, FLIGHT_MS)!;
    expect(b.x).toBeCloseTo(105, 5);
    expect(b.y).toBeCloseTo(35, 5);
  });
  it("进球进网(越过门线);扑救 / 封堵反弹回场内;偏出飞出底线后淡出", () => {
    const line = { kind: "line" as const, x: 105, y: 35, z: 1, real: true };
    expect(ballAt(from, line, RESULT_GOAL, FLIGHT_MS + 400)!.x).toBeGreaterThan(105);
    expect(ballAt(from, line, RESULT_SAVED, FLIGHT_MS + 400)!.x).toBeLessThan(105);
    const block = { kind: "block" as const, x: 95, y: 31, z: 0, real: true };
    expect(ballAt(from, block, RESULT_BLOCKED, FLIGHT_MS + 400)!.x).toBeLessThan(95);
    const miss = ballAt(from, { kind: "line" as const, x: 105, y: 42, z: 0.5, real: true }, RESULT_MISS, FLIGHT_MS + 440)!;
    expect(miss.x).toBeGreaterThan(105);
    expect(miss.alpha).toBeLessThan(0.1);
    expect(ballAt(from, line, RESULT_MISS, FLIGHT_MS + 10_000)).toBeNull();
  });
});

describe("画面坐标", () => {
  it("主队向右 / 向上进攻,客队相反;进攻方右路在进攻方向的右手边", () => {
    // 横版:主队攻右门,右路(y 小)在画面下方
    expect(toStage("landscape", 0, 105, 34)).toEqual([108, 34]);
    expect(toStage("landscape", 0, 90, 10)[1]).toBeGreaterThan(34);
    expect(toStage("landscape", 1, 105, 34)).toEqual([3, 34]);
    expect(toStage("landscape", 1, 90, 10)[1]).toBeLessThan(34); // 面向左时右手边在上方
    // 竖版:主队攻上方球门,右路在画面右侧
    expect(toStage("portrait", 0, 105, 34)).toEqual([34, 3]);
    expect(toStage("portrait", 0, 90, 10)[0]).toBeGreaterThan(34);
    expect(toStage("portrait", 1, 105, 34)).toEqual([34, 108]);
    expect(toStage("portrait", 1, 90, 10)[0]).toBeLessThan(34);
    expect(stageSize("landscape")).toEqual({ w: 111, h: 68 });
    expect(stageSize("portrait")).toEqual({ w: 68, h: 111 });
  });
});
