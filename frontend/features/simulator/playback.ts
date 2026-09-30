// 动画时间线(纯函数):把一场模拟的分钟与事件排到播放时间轴上。
// 1 倍速下一场约 35–45 秒(没有射门的分钟 0.26 秒);同一分钟里的多脚射门错开;进球后停留一会儿(比分跳动、射手卡片);
// 半场停顿一下。组件用真实时钟推进播放时间(不依赖 requestAnimationFrame,隐藏标签页也不会卡住)。

import type { SimEvent, SingleResult } from "./engine";
import { PITCH_LENGTH, PITCH_WIDTH, RESULT_BLOCKED, RESULT_GOAL, RESULT_POST, RESULT_SAVED, type ShotEnd } from "./shotDetail";

export const MS_PER_TICK = 260;
export const SHOT_STAGGER_MS = 300;
export const FLIGHT_MS = 600;
/** 球落定后的余韵(弹出、滚出、进网抖动);下一分钟可以在余韵期间开始 */
export const AFTER_MS = 450;
/** 射门落定后至少再停这么久才进入下一分钟(让人看清结果) */
export const SETTLE_MS = 140;
export const GOAL_HOLD_MS = 1500;
export const HALF_TIME_HOLD_MS = 1200;
export const END_HOLD_MS = 1200;

export interface Plan {
  /** 第 t 分钟开始的播放时间(ms) */
  tickStart: number[];
  /** 第 i 个事件开始的播放时间:射门 = 起脚时刻,其它 = 发生时刻 */
  eventStart: number[];
  /** 第 i 个事件"落定"的播放时间:射门 = 球到达终点,其它 = 发生时刻。比分、统计、解说都在落定时更新 */
  eventResolved: number[];
  /** 半场停顿开始的播放时间(没有下半场时为 null) */
  halfTimeAt: number | null;
  /** 最后一分钟结束 = 终场哨 */
  fullTimeAt: number;
  total: number;
}

const isShot = (e: SimEvent) => e.kind === "shot" || e.kind === "goal";

export function buildPlan(single: Pick<SingleResult, "events" | "totalTicks" | "halfTimeTick">): Plan {
  const byTick = new Map<number, number[]>();
  single.events.forEach((e, i) => {
    const list = byTick.get(e.tick) ?? [];
    list.push(i);
    byTick.set(e.tick, list);
  });
  const tickStart: number[] = [];
  const eventStart = new Array<number>(single.events.length).fill(0);
  const eventResolved = new Array<number>(single.events.length).fill(0);
  let cursor = 0;
  let halfTimeAt: number | null = null;
  for (let t = 0; t < single.totalTicks; t++) {
    if (t === single.halfTimeTick && t > 0) {
      halfTimeAt = cursor;
      cursor += HALF_TIME_HOLD_MS;
    }
    tickStart[t] = cursor;
    let span = MS_PER_TICK;
    let k = 0;
    for (const i of byTick.get(t) ?? []) {
      const e = single.events[i];
      if (isShot(e)) {
        eventStart[i] = cursor + k * SHOT_STAGGER_MS;
        eventResolved[i] = eventStart[i] + FLIGHT_MS;
        k += 1;
        span = Math.max(span, eventResolved[i] - cursor + (e.kind === "goal" ? GOAL_HOLD_MS : SETTLE_MS));
      } else {
        eventStart[i] = cursor;
        eventResolved[i] = cursor;
      }
    }
    cursor += span;
  }
  return { tickStart, eventStart, eventResolved, halfTimeAt, fullTimeAt: cursor, total: cursor + END_HOLD_MS };
}

/** 播放时间 ms 时显示的分钟序号(终场后 = totalTicks) */
export function tickAt(plan: Plan, ms: number): number {
  if (ms >= plan.fullTimeAt) return plan.tickStart.length;
  let lo = 0;
  let hi = plan.tickStart.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (plan.tickStart[mid] <= ms) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

/** 播放时间 ms 时已落定的事件数(事件按落定时间排序后的前缀长度,见 resolvedOrder) */
export function resolvedOrder(plan: Plan): number[] {
  return plan.eventResolved.map((_, i) => i).sort((a, b) => plan.eventResolved[a] - plan.eventResolved[b] || a - b);
}

export interface BallPoint {
  /** 进攻方坐标系(x 0–105,球门在 105;y 0–68) */
  x: number;
  y: number;
  /** 离地高度(米),用来画球的大小与影子 */
  z: number;
  /** 球是否还在(偏出后滚出底线会淡出) */
  alpha: number;
}

const ease = (t: number) => 1 - (1 - t) * (1 - t);

/** 射门第 ms 毫秒(从起脚算)时球的位置;超过 FLIGHT_MS + AFTER_MS 返回 null(球已离场)。 */
export function ballAt(from: [number, number], end: ShotEnd, result: number, ms: number): BallPoint | null {
  if (ms < 0 || ms > FLIGHT_MS + AFTER_MS) return null;
  const [x0, y0] = from;
  if (ms <= FLIGHT_MS) {
    const t = ease(ms / FLIGHT_MS);
    // 飞行高度:终点高度线性插值 + 一段弧线(头球与远射更高一点)
    const arc = Math.sin(Math.PI * t) * Math.min(3, Math.hypot(end.x - x0, end.y - y0) / 12);
    return { x: x0 + (end.x - x0) * t, y: y0 + (end.y - y0) * t, z: end.z * t + arc, alpha: 1 };
  }
  const u = (ms - FLIGHT_MS) / AFTER_MS; // 0–1
  const dx = end.x - x0;
  const dy = end.y - y0;
  const len = Math.hypot(dx, dy) || 1;
  const [ux, uy] = [dx / len, dy / len];
  if (result === RESULT_GOAL) {
    // 进网:再往网里走 1.5 米后停住
    return { x: Math.min(end.x + ux * 1.6 * u, PITCH_LENGTH + 1.8), y: end.y + uy * 1.6 * u, z: end.z * (1 - u), alpha: 1 };
  }
  if (result === RESULT_SAVED || result === RESULT_POST || result === RESULT_BLOCKED) {
    // 被扑出 / 打门框 / 被挡:反弹回场内,偏向一侧
    const side = end.y >= 34 ? 1 : -1;
    const back = result === RESULT_BLOCKED ? 5 : 7;
    return {
      x: end.x - ux * back * u,
      y: clampY(end.y + side * 5 * u),
      z: Math.max(0, end.z * (1 - u) + Math.sin(Math.PI * u)),
      alpha: 1 - Math.max(0, u - 0.6) / 0.4,
    };
  }
  // 偏出 / 高出:继续飞出底线后淡出
  return { x: end.x + ux * 4 * u, y: clampY(end.y + uy * 4 * u), z: end.z + 1.2 * u, alpha: 1 - u };
}

const clampY = (y: number) => Math.max(0, Math.min(PITCH_WIDTH, y));

// ---- 画面坐标 ----
// 动画画布 = 球场 + 两端各 NET_MARGIN 米(画球网、让球能"进网")。
// 横版(页面内):主队向右进攻;竖版(录屏模式):主队向上进攻。
// 进攻方坐标:x 0–105(球门在 105)、y 0–68,y < 34 = 进攻方右路(docs/data-sources.md §1.4.3)。

export type StageOrientation = "landscape" | "portrait";
export const NET_MARGIN = 3;
export const NET_DEPTH = 2;

export function stageSize(o: StageOrientation): { w: number; h: number } {
  const long = PITCH_LENGTH + 2 * NET_MARGIN;
  return o === "landscape" ? { w: long, h: PITCH_WIDTH } : { w: PITCH_WIDTH, h: long };
}

/** 进攻方坐标 → 画布坐标(米,原点在画布左上角) */
export function toStage(o: StageOrientation, team: 0 | 1, x: number, y: number): [number, number] {
  if (o === "landscape") {
    // 面向右时进攻方的右手边在画面下方:y 小(右路)→ 画面靠下
    return team === 0 ? [NET_MARGIN + x, PITCH_WIDTH - y] : [NET_MARGIN + PITCH_LENGTH - x, y];
  }
  // 面向上时进攻方的右手边在画面右侧
  return team === 0 ? [PITCH_WIDTH - y, NET_MARGIN + PITCH_LENGTH - x] : [y, NET_MARGIN + x];
}
