// 模拟射门 → 真实射门细节(位置、射门方式、结果、终点),只给动画与文字直播用。
//
// - 从参数文件的射门样本库(scripts/simulator/shot_samples.py)里,按同渠道、同 xG 箱抽一脚真实射门;
//   进球用它的位置与射门方式,没进的再用它的真实结果(扑救 / 封堵 / 偏出 / 门框)。
// - 点球固定在点球点;乌龙球不从样本库抽。
// - 随机数:每个事件一条独立的流,由"模拟编号 + 事件序号"派生(eventRng),不碰模拟本身的随机数流——
//   同一模拟编号的比分与事件与改动前完全一致(tests/simulator-shot-detail.test.ts 用改动前的指纹核对)。
// - 结果快照里保存抽到的细节(SimEvent.sd),分享链接打开时原样使用,不因参数更新而变。

import type { SimEvent } from "./engine";
import { mulberry32, type Rng } from "./rng";
import type { ShotChannel, ShotSample, ShotSampleLibrary } from "./types";

export const PITCH_LENGTH = 105;
export const PITCH_WIDTH = 68;
export const GOAL_Y_MIN = 30.34;
export const GOAL_Y_MAX = 37.66;
export const CROSSBAR_Z = 2.44;
export const PENALTY_SPOT: [number, number] = [94, 34];

export const RESULT_GOAL = 0;
export const RESULT_SAVED = 1;
export const RESULT_BLOCKED = 2;
export const RESULT_MISS = 3;
export const RESULT_POST = 4;
export type ShotResult = "goal" | "saved" | "blocked" | "miss" | "post";
export const RESULT_NAME: ShotResult[] = ["goal", "saved", "blocked", "miss", "post"];
export type BodyPart = "right" | "left" | "head" | "other";
export const BODY_NAME: BodyPart[] = ["right", "left", "head", "other"];

/** 32 位整数混合(murmur3 fmix32) */
function fmix32(h: number): number {
  h ^= h >>> 16;
  h = Math.imul(h, 0x85ebca6b);
  h ^= h >>> 13;
  h = Math.imul(h, 0xc2b2ae35);
  h ^= h >>> 16;
  return h >>> 0;
}

/** 事件 i 的独立随机数流:由 (模拟编号, 事件序号, 用途) 派生。用途区分射门细节与解说句式,互不影响。 */
export function eventRng(seed: number, index: number, purpose = 0): Rng {
  return mulberry32(fmix32(fmix32((seed >>> 0) ^ Math.imul(index + 1, 0x9e3779b1)) ^ Math.imul(purpose + 1, 0x7feb352d)));
}

/** 与 scripts/simulator/shot_samples.py::bin_index 同一口径:edges[i] ≤ xg < edges[i+1],超界夹到首末箱 */
export function binOf(edges: number[], xg: number): number {
  let i = 0;
  while (i + 1 < edges.length && edges[i + 1] <= xg) i += 1;
  return Math.max(0, Math.min(edges.length - 2, i));
}

function pickFrom<T>(rng: Rng, items: T[]): T {
  return items[Math.floor(rng() * items.length) % items.length];
}

/** 从 bin 开始向两侧找第一个满足条件的非空候选集 */
function nearestPool(bins: ShotSample[][], bin: number, keep: (s: ShotSample) => boolean): ShotSample[] {
  for (let d = 0; d < bins.length; d++) {
    for (const b of d === 0 ? [bin] : [bin - d, bin + d]) {
      if (b < 0 || b >= bins.length) continue;
      const pool = bins[b].filter(keep);
      if (pool.length) return pool;
    }
  }
  return [];
}

const r2 = (v: number) => Math.round(v * 100) / 100;

/** 进球的越线点:样本本身是进球且越线点在门框内就用它,否则在门框内补一个 */
function goalEnd(sample: ShotSample, rng: Rng): [number, number] {
  if (sample[3] === RESULT_GOAL && sample.length === 6 && sample[4] > GOAL_Y_MIN && sample[4] < GOAL_Y_MAX && sample[5] < CROSSBAR_Z) {
    return [sample[4], sample[5]];
  }
  return [r2(GOAL_Y_MIN + 0.4 + rng() * (GOAL_Y_MAX - GOAL_Y_MIN - 0.8)), r2(0.1 + rng() * 2)];
}

function sampleChannel(e: SimEvent): ShotChannel {
  if (e.channel === "counter" || e.channel === "setpiece" || e.channel === "penalty") return e.channel;
  return "open"; // 运动战与门将失误都从运动战样本里抽(门将失误按它的 xG 0.5 落在近距离箱)
}

/** 示意位置(旧参数文件没有样本库时):按 xG 由远到近,且至少在禁区线附近 */
function fallbackSample(e: SimEvent, rng: Rng): ShotSample {
  const close = Math.min(e.xg ?? 0.1, 0.6) / 0.6;
  const x = r2(PITCH_LENGTH - (5 + (1 - close) * 17 + rng() * 3));
  const y = r2(34 + (rng() - 0.5) * (30 - close * 18));
  return [x, y, Math.floor(rng() * 3), e.isGoal ? RESULT_GOAL : pickFrom(rng, [RESULT_SAVED, RESULT_BLOCKED, RESULT_MISS])];
}

/** 一脚模拟射门的细节;不是射门/进球事件返回 null */
export function shotDetailFor(e: SimEvent, index: number, seed: number, lib: ShotSampleLibrary | undefined): ShotSample | null {
  if (e.kind !== "shot" && e.kind !== "goal") return null;
  const rng = eventRng(seed, index, 0);

  if (e.channel === "owngoal") {
    // 乌龙:防守方在本方门前把球弄进自家球门(坐标仍按受益方的进攻方向记,球门在 x=105)
    const x = r2(PITCH_LENGTH - (1.5 + rng() * 6));
    const y = r2(34 + (rng() - 0.5) * 14);
    const [gy, gz] = [r2(GOAL_Y_MIN + 0.5 + rng() * 6.3), r2(0.1 + rng() * 1.2)];
    return [x, y, 3, RESULT_GOAL, gy, gz];
  }

  const ch = sampleChannel(e);
  const table = lib?.channels?.[ch];
  if (!table || !table.bins?.length) {
    const s = fallbackSample(e, rng);
    return e.channel === "penalty" ? [PENALTY_SPOT[0], PENALTY_SPOT[1], s[2], s[3]] : s;
  }
  const bin = ch === "penalty" ? 0 : binOf(table.edges, e.xg ?? 0);

  if (e.isGoal) {
    // 优先用本箱里的真实进球(越线点也真实);本箱没有进球就用本箱全部样本的位置,本箱为空再向两侧找
    const here = table.bins[bin] ?? [];
    const goalsHere = here.filter((s) => s[3] === RESULT_GOAL);
    let pool = goalsHere.length ? goalsHere : here;
    if (!pool.length) pool = nearestPool(table.bins, bin, () => true);
    const s = pool.length ? pickFrom(rng, pool) : fallbackSample(e, rng);
    const [gy, gz] = goalEnd(s, rng);
    const [x, y] = ch === "penalty" ? PENALTY_SPOT : [s[0], s[1]];
    return [x, y, s[2], RESULT_GOAL, gy, gz];
  }

  const pool = nearestPool(table.bins, bin, (s) => s[3] !== RESULT_GOAL);
  const s = pool.length ? pickFrom(rng, pool) : fallbackSample(e, rng);
  if (ch === "penalty") {
    // 点球被封堵不存在:样本里即便有也按扑救处理
    const res = s[3] === RESULT_BLOCKED ? RESULT_SAVED : s[3];
    return s.length === 6 && res !== RESULT_BLOCKED
      ? [PENALTY_SPOT[0], PENALTY_SPOT[1], s[2], res, s[4], s[5]]
      : [PENALTY_SPOT[0], PENALTY_SPOT[1], s[2], res];
  }
  return [...s] as ShotSample;
}

/** 给事件流里还没有细节的射门补上细节;已有的(来自结果快照 / 分享链接)原样保留 */
export function withShotDetails(events: SimEvent[], seed: number, lib: ShotSampleLibrary | undefined): SimEvent[] {
  return events.map((e, i) => {
    if (e.sd || (e.kind !== "shot" && e.kind !== "goal")) return e;
    const sd = shotDetailFor(e, i, seed, lib);
    return sd ? { ...e, sd } : e;
  });
}

export interface ShotEnd {
  kind: "line" | "block";
  x: number;
  y: number;
  /** 越线时离地高度(米);封堵时为 0 */
  z: number;
  /** 终点来自真实数据(否则是按结果补的示意终点) */
  real: boolean;
}

/** 射门终点:有真实越线点 / 封堵点就用,缺失时按结果在合理范围里补一个(确定性,取自同一事件流) */
export function shotEnd(sd: ShotSample, seed: number, index: number): ShotEnd {
  const [x, y, , res] = sd;
  if (sd.length === 6) {
    if (res === RESULT_BLOCKED) return { kind: "block", x: sd[4], y: sd[5], z: 0, real: true };
    return { kind: "line", x: PITCH_LENGTH, y: sd[4], z: sd[5], real: true };
  }
  const rng = eventRng(seed, index, 2);
  if (res === RESULT_BLOCKED) {
    const t = 0.25 + rng() * 0.25; // 封堵点在射门点到球门中心连线的前 1/4–1/2
    return { kind: "block", x: r2(x + (PITCH_LENGTH - x) * t), y: r2(y + (34 - y) * t), z: 0, real: false };
  }
  if (res === RESULT_MISS) {
    const wide = rng() < 0.6;
    const side = rng() < 0.5 ? -1 : 1;
    return wide
      ? { kind: "line", x: PITCH_LENGTH, y: r2(34 + side * (4.2 + rng() * 5)), z: r2(rng() * 1.5), real: false }
      : { kind: "line", x: PITCH_LENGTH, y: r2(GOAL_Y_MIN + rng() * 7.3), z: r2(2.6 + rng() * 2), real: false };
  }
  if (res === RESULT_POST) {
    const bar = rng() < 0.3;
    return bar
      ? { kind: "line", x: PITCH_LENGTH, y: r2(GOAL_Y_MIN + 1 + rng() * 5.3), z: CROSSBAR_Z, real: false }
      : { kind: "line", x: PITCH_LENGTH, y: rng() < 0.5 ? GOAL_Y_MIN : GOAL_Y_MAX, z: r2(0.3 + rng() * 1.8), real: false };
  }
  return { kind: "line", x: PITCH_LENGTH, y: r2(GOAL_Y_MIN + 0.4 + rng() * 6.5), z: r2(0.1 + rng() * 2), real: false };
}

export interface ShotZone {
  /** 小禁区 / 禁区内 / 禁区外 */
  area: "six" | "box" | "outside";
  /** 进攻方视角:左路 / 中路 / 右路(y > 34 = 左) */
  side: "left" | "center" | "right";
  /** 离球门中心的距离(米) */
  distance: number;
}

export function zoneOf(x: number, y: number): ShotZone {
  const dy = y - 34;
  const area = x >= PITCH_LENGTH - 5.5 && Math.abs(dy) <= 9.16 ? "six" : x >= PITCH_LENGTH - 16.5 && Math.abs(dy) <= 20.16 ? "box" : "outside";
  const side = Math.abs(dy) <= 7 ? "center" : dy > 0 ? "left" : "right";
  return { area, side, distance: Math.hypot(PITCH_LENGTH - x, dy) };
}

/** 未进球的细分:高出 / 偏出(从进攻方看偏左、偏右)/ 门柱 / 横梁 */
export function missKind(sd: ShotSample, end: ShotEnd): "high" | "wideLeft" | "wideRight" | "wide" | "post" | "bar" | null {
  const res = sd[3];
  if (res === RESULT_POST) return end.z >= CROSSBAR_Z - 0.15 && end.y > GOAL_Y_MIN + 0.3 && end.y < GOAL_Y_MAX - 0.3 ? "bar" : "post";
  if (res !== RESULT_MISS) return null;
  if (end.y > GOAL_Y_MIN && end.y < GOAL_Y_MAX && end.z > CROSSBAR_Z) return "high";
  if (!end.real) return "wide";
  return end.y >= GOAL_Y_MAX ? "wideLeft" : "wideRight";
}
