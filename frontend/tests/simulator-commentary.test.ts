// @vitest-environment node
/**
 * 文字直播模板:禁用词、占位符、句式多样、与射门细节一致;实时统计口径。
 */
import { describe, expect, it } from "vitest";
import { prepareMatch, simulateOnce, type SimEvent } from "@/features/simulator/engine";
import { BANNED_WORDS, commentLine, fullTimeLine, halfTimeLine, kickoffLine, liveStats, zonePhrase } from "@/features/simulator/commentary";
import { mulberry32 } from "@/features/simulator/rng";
import { withShotDetails } from "@/features/simulator/shotDetail";
import type { ShotSample, ShotSampleLibrary } from "@/features/simulator/types";
import { cumulativeXg } from "@/features/simulator/xg";
import { matchSetup, simParams } from "./fixtures/simulatorParams";

const NAMES: [string, string] = ["阿森纳", "曼城"];

function lib(): ShotSampleLibrary {
  const bins = (xs: ShotSample[]) => Array.from({ length: 4 }, () => xs);
  const all: ShotSample[] = [
    [101, 34, 2, 1, 33, 1.2], [94, 44, 1, 2, 97, 40], [80, 34, 0, 3, 34, 3.2], [92, 20, 0, 3, 40, 0.3],
    [95, 30, 1, 3, 26, 0.2], [96, 38, 0, 4, 37.66, 1], [99, 33, 2, 4, 34, 2.4], [90, 36, 0, 0, 35, 0.5], [85, 28, 1, 3],
  ];
  return {
    version: 1, fields: [],
    channels: {
      open: { edges: [0, 0.05, 0.1, 0.3, 1], bins: bins(all) },
      counter: { edges: [0, 0.05, 0.1, 0.3, 1], bins: bins(all) },
      setpiece: { edges: [0, 0.05, 0.1, 0.3, 1], bins: bins(all) },
      penalty: { edges: [0, 1], bins: [[[94, 34, 0, 1], [94, 34, 0, 3], [94, 34, 0, 4], [94, 34, 0, 0]]] },
    },
  };
}

/** 覆盖各种事件的合成比赛 + 真实引擎跑出的若干场 */
function corpus(): { events: SimEvent[]; seed: number }[] {
  const out: { events: SimEvent[]; seed: number }[] = [];
  const r = prepareMatch(simParams(), matchSetup());
  if (!r.ok) throw new Error(r.error);
  for (let seed = 1; seed <= 60; seed++) {
    const s = simulateOnce(r.config, seed);
    out.push({ events: withShotDetails(s.events, seed, lib()), seed });
  }
  const base = { tick: 5, minute: 6, added: 0, clock: "6'" };
  const synthetic: SimEvent[] = [];
  let score: [number, number] = [0, 0];
  for (const channel of ["open", "counter", "setpiece", "penalty", "gk_error", "owngoal"] as const) {
    for (const kind of ["shot", "goal"] as const) {
      if (channel === "owngoal" && kind === "shot") continue;
      if (kind === "goal") score = [score[0] + 1, score[1]];
      synthetic.push({ ...base, team: 0, kind, channel, playerId: "h9", playerName: "萨卡", xg: channel === "owngoal" ? undefined : 0.2, isGoal: kind === "goal", score });
    }
  }
  synthetic.push({ ...base, team: 1, kind: "red", playerId: "a3", playerName: "罗德里" });
  synthetic.push({ ...base, team: 1, kind: "injury" });
  synthetic.push({ ...base, team: 0, kind: "shot", channel: "open", xg: 0.1, isGoal: false }); // 没有球员名
  for (let seed = 1; seed <= 30; seed++) out.push({ events: withShotDetails(synthetic, seed, lib()), seed });
  return out;
}

function allLines(): string[] {
  const lines: string[] = [];
  for (const { events, seed } of corpus()) {
    events.forEach((_, i) => {
      const l = commentLine(events, i, seed, NAMES);
      if (l) lines.push(l);
    });
    lines.push(kickoffLine(seed, NAMES));
    for (const s of [[0, 0], [2, 1], [0, 3], [4, 0]] as [number, number][]) {
      lines.push(halfTimeLine(seed, NAMES, s), fullTimeLine(seed, NAMES, s));
    }
  }
  return lines;
}

describe("文字直播模板", () => {
  const lines = allLines();

  it("每句都不含禁用词(预测 / 推荐 / 稳 / 让球 / 赢盘 等)", () => {
    for (const w of ["预测", "推荐", "稳", "让球", "赢盘"]) expect(BANNED_WORDS).toContain(w);
    const bad = lines.filter((l) => BANNED_WORDS.some((w) => l.includes(w)));
    expect(bad).toEqual([]);
  });

  it("没有未填的占位符、undefined、NaN,也不出现英文结果词", () => {
    const bad = lines.filter((l) => /[{}]|undefined|NaN|null|Goal|Miss|Saved/.test(l));
    expect(bad).toEqual([]);
  });

  it("句式足够多样(不同句子 ≥ 60 种),每句以中文标点结尾", () => {
    expect(new Set(lines).size).toBeGreaterThanOrEqual(60);
    expect(lines.every((l) => /[。！]$/.test(l))).toBe(true);
  });

  it("同一编号同一事件总是同一句", () => {
    const { events, seed } = corpus()[0];
    events.forEach((_, i) => expect(commentLine(events, i, seed, NAMES)).toBe(commentLine(events, i, seed, NAMES)));
  });

  it("句子与射门细节一致:进球有比分,扑救 / 封堵 / 门柱 / 横梁 / 高出各有对应说法", () => {
    const at = (sd: ShotSample, kind: "shot" | "goal" = "shot"): string =>
      commentLine([{ tick: 1, minute: 2, added: 0, clock: "2'", team: 0, kind, channel: "open", playerName: "萨卡", xg: 0.1, isGoal: kind === "goal", score: [1, 0], sd }], 0, 3, NAMES)!;
    expect(at([90, 34, 0, 0, 35, 0.5], "goal")).toContain("阿森纳 1:0 曼城");
    expect(at([101, 34, 2, 1, 33, 1.2])).toMatch(/门将/);
    expect(at([94, 44, 1, 2, 97, 40])).toMatch(/挡|封堵|防守球员/);
    expect(at([96, 38, 0, 4, 37.66, 1])).toMatch(/门柱/);
    expect(at([99, 33, 2, 4, 34, 2.4])).toMatch(/横梁/);
    expect(at([80, 34, 0, 3, 34, 3.2])).toMatch(/高|看台/);
    expect(at([92, 20, 0, 3, 40, 0.3])).toMatch(/左/);
    expect(at([95, 30, 1, 3, 26, 0.2])).toMatch(/右/);
    expect(at([101, 34, 2, 1, 33, 1.2])).toMatch(/头球|甩头/);
  });

  it("梅开二度 / 帽子戏法;乌龙写明是哪队球员", () => {
    const g = (i: number): SimEvent => ({ tick: i, minute: i + 1, added: 0, clock: `${i + 1}'`, team: 0, kind: "goal", channel: "open", playerId: "h9", playerName: "萨卡", xg: 0.3, isGoal: true, score: [i + 1, 0], sd: [95, 34, 0, 0, 35, 0.5] });
    const evs = [g(0), g(1), g(2)];
    expect(commentLine(evs, 1, 1, NAMES)).toContain("梅开二度");
    expect(commentLine(evs, 2, 1, NAMES)).toContain("帽子戏法");
    const og: SimEvent = { tick: 1, minute: 2, added: 0, clock: "2'", team: 0, kind: "goal", channel: "owngoal", playerName: "迪亚斯", isGoal: true, score: [1, 0] };
    expect(commentLine([og], 0, 1, NAMES)).toMatch(/乌龙/);
  });

  it("射门位置说法:禁区内外、左中右(进攻方视角,y > 34 = 左)", () => {
    const r = mulberry32(1);
    expect(zonePhrase(94, 45, r)).toBe("在禁区左侧");
    expect(zonePhrase(94, 23, r)).toBe("在禁区右侧");
    expect(zonePhrase(94, 34, r)).toBe("在点球点附近");
    expect(zonePhrase(84, 34, r)).toBe("在禁区弧顶");
    expect(zonePhrase(70, 34, r)).toBe("在三十米开外");
    expect(zonePhrase(80, 55, r)).toBe("在禁区外左侧");
  });
});

describe("实时统计", () => {
  it("射门不含乌龙;射正 = 进球 + 扑救;xG 与赛跑图同源", () => {
    const e = (over: Partial<SimEvent>): SimEvent => ({ tick: 1, minute: 2, added: 0, clock: "2'", team: 0, kind: "shot", channel: "open", xg: 0.1, ...over });
    const events: SimEvent[] = [
      e({ sd: [90, 34, 0, 1] }), // 扑救 → 射正
      e({ sd: [90, 34, 0, 2] }), // 封堵
      e({ sd: [90, 34, 0, 4] }), // 门柱(不算射正)
      e({ kind: "goal", isGoal: true, sd: [90, 34, 0, 0] }),
      e({ team: 1, kind: "goal", channel: "owngoal", xg: undefined, isGoal: true }),
      e({ team: 1, tick: 50, sd: [90, 34, 0, 1] }),
    ];
    const s = liveStats(events, 10);
    expect(s.shots).toEqual([4, 0]);
    expect(s.onTarget).toEqual([2, 0]);
    expect(s.xg).toEqual(cumulativeXg(events, 10));
    expect(liveStats(events, 99).shots).toEqual([4, 1]);
  });
});

describe("定位球说法与位置一致", () => {
  const at = (sd: ShotSample, kind: "shot" | "goal", seed: number) =>
    commentLine([{ tick: 1, minute: 2, added: 0, clock: "2'", team: 0, kind, channel: "setpiece", playerName: "厄德高", xg: 0.05, isGoal: kind === "goal", score: [1, 0], sd }], 0, seed, NAMES)!;
  it("禁区外的定位球射门说成直接任意球,不说\"吊进来\"\"角球\"", () => {
    for (let seed = 1; seed <= 40; seed++) {
      for (const kind of ["shot", "goal"] as const) {
        const l = at([78, 34, 1, kind === "goal" ? 0 : 1, 35, 1], kind, seed);
        expect(l).toMatch(/任意球/);
        expect(l).not.toMatch(/吊进来|角球|抢点|混战/);
      }
    }
  });
});

describe("射门方式与说法一致", () => {
  it("头球的句子不出现\"起脚\"\"脚\";右脚 / 左脚的句子不出现\"头球\"", () => {
    const line = (body: number, kind: "shot" | "goal", channel: "open" | "setpiece" | "counter", seed: number, x = 95) =>
      commentLine([{ tick: 1, minute: 2, added: 0, clock: "2'", team: 0, kind, channel, playerName: "廷伯", xg: 0.1, isGoal: kind === "goal", score: [1, 0], sd: [x, 34, body, kind === "goal" ? 0 : 1, 35, 1] }], 0, seed, NAMES)!;
    for (let seed = 1; seed <= 60; seed++) {
      for (const kind of ["shot", "goal"] as const) {
        for (const ch of ["open", "setpiece", "counter"] as const) {
          for (const x of [95, 78]) {
            expect(line(2, kind, ch, seed, x)).not.toMatch(/脚/);
            expect(line(0, kind, ch, seed, x)).not.toMatch(/头球|甩头/);
            expect(line(1, kind, ch, seed, x)).not.toMatch(/头球|甩头/);
          }
        }
      }
    }
  });
});
