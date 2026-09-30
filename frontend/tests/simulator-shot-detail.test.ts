// @vitest-environment node
/**
 * 射门细节(真实射门样本库):可复现、不影响模拟本身、分享链接原样使用、点球 / 乌龙规则。
 */
import { describe, expect, it } from "vitest";
import { prepareMatch, simulateOnce, type SimEvent } from "@/features/simulator/engine";
import {
  binOf,
  eventRng,
  missKind,
  PENALTY_SPOT,
  RESULT_BLOCKED,
  RESULT_GOAL,
  shotDetailFor,
  shotEnd,
  withShotDetails,
  zoneOf,
} from "@/features/simulator/shotDetail";
import { ensureShotDetails, makeSnapshot } from "@/features/simulator/snapshot";
import { decodeResult, encodeResult } from "@/features/simulator/shareLink";
import type { ShotSample, ShotSampleLibrary } from "@/features/simulator/types";
import { fnv1a, matchSetup, simParams } from "./fixtures/simulatorParams";

// 改动前(2026-09-30,引擎未改)用同一份夹具算出的指纹:[模拟编号, 比分, 事件数, simulateOnce 整体 JSON 的 FNV-1a]
const GOLDEN: [number, string, number, string][] = [
  [1, "4-2", 31, "a012ad6d"],
  [7, "1-0", 28, "4e9c77be"],
  [4242, "0-1", 32, "bc263ddb"],
  [20260930, "2-4", 22, "b790de59"],
  [123456789, "0-1", 27, "44d9afec"],
];

function lib(): ShotSampleLibrary {
  const edges = [0, 0.05, 0.1, 0.2, 0.4, 1];
  const bin = (b: number): ShotSample[] => [
    [80 + b * 4, 30, 0, 1, 33, 1.2], // 扑救
    [80 + b * 4, 40, 1, 2, 90, 38], // 封堵(终点 = 封堵点)
    [80 + b * 4, 34, 2, 3, 40.5, 0.4], // 偏出
    [80 + b * 4, 28, 0, 4, 30.34, 1.1], // 门柱
    [80 + b * 4, 36, 1, 0, 35, 0.8], // 进球
  ];
  return {
    version: 1,
    fields: ["x", "y", "type", "result", "a", "b"],
    channels: {
      open: { edges, bins: [0, 1, 2, 3, 4].map(bin) },
      counter: { edges, bins: [0, 1, 2, 3, 4].map(bin) },
      setpiece: { edges, bins: [[], [], [], [], []] },
      penalty: { edges: [0, 1], bins: [[[94, 34, 0, 1, 33.1, 0.4], [94, 34, 0, 2], [94, 34, 1, 0, 36.9, 0.5]]] },
    },
  };
}

function run(seed: number) {
  const r = prepareMatch(simParams(), matchSetup());
  if (!r.ok) throw new Error(r.error);
  return { config: r.config, single: simulateOnce(r.config, seed) };
}

describe("模拟本身不受影响", () => {
  it("同一编号的比分与事件与改动前完全一致(指纹)", () => {
    for (const [seed, score, n, hash] of GOLDEN) {
      const { single } = run(seed);
      expect([seed, single.score.join("-"), single.events.length, fnv1a(JSON.stringify(single))]).toEqual([seed, score, n, hash]);
    }
  });

  it("补上射门细节只多一个 sd 字段,其余字段逐一不变", () => {
    const { single } = run(4242);
    const decorated = withShotDetails(single.events, single.seed, lib());
    const strip = (e: SimEvent): SimEvent => Object.fromEntries(Object.entries(e).filter(([k]) => k !== "sd")) as SimEvent;
    expect(decorated.map(strip)).toEqual(single.events);
    expect(decorated.filter((e) => e.kind === "shot" || e.kind === "goal").every((e) => Array.isArray(e.sd))).toBe(true);
    expect(decorated.filter((e) => e.kind !== "shot" && e.kind !== "goal").every((e) => e.sd === undefined)).toBe(true);
  });
});

describe("可复现", () => {
  it("同一 (编号, 事件序号) 抽到同一脚;换编号或换序号会变", () => {
    const a = eventRng(20260930, 5)();
    expect(eventRng(20260930, 5)()).toBe(a);
    expect(eventRng(20260930, 6)()).not.toBe(a);
    expect(eventRng(20260931, 5)()).not.toBe(a);
    expect(eventRng(20260930, 5, 1)()).not.toBe(a); // 解说句式用另一条流
  });

  it("两次补细节结果一致;已有细节原样保留", () => {
    const { single } = run(7);
    const a = withShotDetails(single.events, 7, lib());
    expect(withShotDetails(single.events, 7, lib())).toEqual(a);
    const kept = withShotDetails(a, 7, undefined); // 没有样本库也不覆盖已有细节
    expect(kept).toEqual(a);
  });

  it("分享链接往返后射门细节不变;旧链接(无细节)按同一规则补上", async () => {
    const params = { ...simParams(), shot_samples: lib() };
    const r = prepareMatch(params, matchSetup());
    if (!r.ok) throw new Error(r.error);
    const single = simulateOnce(r.config, 4242);
    const snap = makeSnapshot(params, matchSetup(), r.config, single, { runs: 0, pHome: 0, pDraw: 0, pAway: 0, meanGoals: [0, 0], topScores: [], scorerProb: [], upset: { outcomeShare: 0 }, pBigMargin: 0, meanReds: 0, meanPenalties: 0, goalBuckets: [], crown: null, fairAhLine: 0, fairOuLine: 2.5 } as never);
    const back = await decodeResult(await encodeResult(snap));
    expect(back!.single.events.map((e) => e.sd)).toEqual(snap.single.events.map((e) => e.sd));
    // 参数换了(样本库变了)也不影响已分享的结果
    expect(ensureShotDetails(back!, { ...params, shot_samples: undefined }).single.events).toEqual(snap.single.events);
    // 旧链接:去掉细节后补回来 = 原来的细节
    const old = { ...snap, single: { ...snap.single, events: snap.single.events.map((e) => Object.fromEntries(Object.entries(e).filter(([k]) => k !== "sd")) as SimEvent) } };
    expect(ensureShotDetails(old, params).single.events.map((e) => e.sd)).toEqual(snap.single.events.map((e) => e.sd));
  });
});

describe("抽样规则", () => {
  const shot = (over: Partial<SimEvent>): SimEvent => ({ tick: 10, minute: 11, added: 0, clock: "11'", team: 0, kind: "shot", channel: "open", xg: 0.07, isGoal: false, ...over });

  it("分箱与后端同一口径", () => {
    const edges = [0, 0.05, 0.1, 1];
    expect(binOf(edges, 0)).toBe(0);
    expect(binOf(edges, 0.05)).toBe(1);
    expect(binOf(edges, 0.0999)).toBe(1);
    expect(binOf(edges, 2)).toBe(2);
  });

  it("没进的射门取样本的真实结果(非进球);进的射门结果是进球,且优先取本箱的真实进球", () => {
    for (let i = 0; i < 60; i++) {
      const miss = shotDetailFor(shot({}), i, 99, lib())!;
      expect(miss[3]).not.toBe(RESULT_GOAL);
      expect(miss[0]).toBe(84); // xG 0.07 → 第 2 箱(80 + 1×4)
      const goal = shotDetailFor(shot({ kind: "goal", isGoal: true }), i, 99, lib())!;
      expect(goal[3]).toBe(RESULT_GOAL);
      expect(goal.slice(0, 3)).toEqual([84, 36, 1]);
    }
  });

  it("本箱为空时向两侧找(定位球表是空的 → 退回示意位置)", () => {
    const sd = shotDetailFor(shot({ channel: "setpiece" }), 3, 99, lib())!;
    expect(sd).toHaveLength(4);
    expect(sd[0]).toBeGreaterThan(80);
  });

  it("点球固定在点球点;没进的点球不会是封堵", () => {
    for (let i = 0; i < 40; i++) {
      const sd = shotDetailFor(shot({ channel: "penalty", xg: 0.79 }), i, 5, lib())!;
      expect(sd.slice(0, 2)).toEqual(PENALTY_SPOT);
      expect(sd[3]).not.toBe(RESULT_BLOCKED);
      expect(sd[3]).not.toBe(RESULT_GOAL);
    }
    const g = shotDetailFor(shot({ channel: "penalty", kind: "goal", isGoal: true, xg: 0.79 }), 0, 5, lib())!;
    expect(g.slice(0, 2)).toEqual(PENALTY_SPOT);
    expect(g[3]).toBe(RESULT_GOAL);
  });

  it("乌龙不从样本库抽:在门前、结果为进球", () => {
    const sd = shotDetailFor(shot({ channel: "owngoal", kind: "goal", isGoal: true, xg: undefined }), 2, 5, lib())!;
    expect(sd[3]).toBe(RESULT_GOAL);
    expect(sd[0]).toBeGreaterThan(97);
    expect(sd[2]).toBe(3);
  });

  it("没有样本库时退回示意位置,仍可复现", () => {
    expect(shotDetailFor(shot({}), 1, 5, undefined)).toEqual(shotDetailFor(shot({}), 1, 5, undefined));
  });
});

describe("终点与位置描述", () => {
  it("有真实终点就用;封堵的终点是封堵点", () => {
    expect(shotEnd([90, 30, 0, 3, 40.5, 0.4], 1, 1)).toMatchObject({ kind: "line", x: 105, y: 40.5, z: 0.4, real: true });
    expect(shotEnd([90, 30, 0, 2, 95, 31], 1, 1)).toMatchObject({ kind: "block", x: 95, y: 31, real: true });
  });

  it("补的示意终点符合结果:偏出在门框外或横梁上方,进球/扑救在门框内", () => {
    for (let i = 0; i < 50; i++) {
      const m = shotEnd([90, 30, 0, 3], 7, i);
      expect(m.y < 30.34 || m.y > 37.66 || m.z > 2.44).toBe(true);
      const s = shotEnd([90, 30, 0, 1], 7, i);
      expect(s.y).toBeGreaterThan(30.34);
      expect(s.y).toBeLessThan(37.66);
      expect(s.z).toBeLessThan(2.44);
    }
  });

  it("高出 / 偏左 / 偏右 / 门柱 / 横梁", () => {
    const k = (sd: ShotSample) => missKind(sd, shotEnd(sd, 1, 1));
    expect(k([90, 30, 0, 3, 34, 3.1])).toBe("high");
    expect(k([90, 30, 0, 3, 40, 0.5])).toBe("wideLeft"); // y > 34 = 进攻方左侧
    expect(k([90, 30, 0, 3, 28, 0.5])).toBe("wideRight");
    expect(k([90, 30, 0, 4, 30.34, 1])).toBe("post");
    expect(k([90, 30, 0, 4, 34, 2.4])).toBe("bar");
  });

  it("区域:小禁区 / 禁区内左右 / 禁区外", () => {
    expect(zoneOf(101, 34).area).toBe("six");
    expect(zoneOf(94, 44)).toMatchObject({ area: "box", side: "left" });
    expect(zoneOf(94, 22)).toMatchObject({ area: "box", side: "right" });
    expect(zoneOf(80, 34)).toMatchObject({ area: "outside", side: "center" });
  });
});
