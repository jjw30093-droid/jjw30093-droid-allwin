// 动画画布的逐帧绘制(命令式,不经过 React):已结束的射门点、飞行中的球与轨迹、按结果收尾的效果。
// 画布按设备像素比渲染;调用方负责尺寸与计时(MatchAnimation 用真实时钟驱动)。

import { AFTER_MS, ballAt, FLIGHT_MS, NET_DEPTH, NET_MARGIN, stageSize, toStage, type StageOrientation } from "@/features/simulator/playback";
import {
  GOAL_Y_MAX,
  GOAL_Y_MIN,
  PITCH_LENGTH,
  RESULT_BLOCKED,
  RESULT_GOAL,
  RESULT_POST,
  RESULT_SAVED,
  type ShotEnd,
} from "@/features/simulator/shotDetail";
import type { ShotSample } from "@/features/simulator/types";

export interface ShotAnim {
  index: number;
  team: 0 | 1;
  sd: ShotSample;
  end: ShotEnd;
  /** 起脚的播放时间(ms) */
  start: number;
}

export interface CanvasColors {
  team: [string, string];
  gold: string;
  ink: string;
  ball: string;
  net: string;
}

/** 从元素上读取主题色(CSS 变量);画布不能直接用 var(),主题切换后下一次读取生效 */
export function readColors(el: Element): CanvasColors {
  const cs = getComputedStyle(el);
  const v = (name: string, fallback: string) => cs.getPropertyValue(name).trim() || fallback;
  return {
    team: [v("--sim-home-pitch", "#0b7a75"), v("--sim-away-pitch", "#b5540b")],
    gold: v("--brand-gold", "#e8b923"),
    ink: v("--ink", "#0d2c3d"),
    ball: "#ffffff",
    net: v("--ink-3", "#7a8a90"),
  };
}

const TAU = Math.PI * 2;

export function drawFrame(
  ctx: CanvasRenderingContext2D,
  o: StageOrientation,
  pxPerM: number,
  ms: number,
  shots: ShotAnim[],
  colors: CanvasColors,
): { flying: number } {
  const { w, h } = stageSize(o);
  ctx.setTransform(pxPerM, 0, 0, pxPerM, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const P = (team: 0 | 1, x: number, y: number) => toStage(o, team, x, y);

  // 两端球网(静态);最近 700ms 内有进球的那一端抖一下
  for (const attacking of [0, 1] as const) {
    const lastGoal = shots.find((s) => s.sd[3] === RESULT_GOAL && s.team === attacking && ms - (s.start + FLIGHT_MS) >= 0 && ms - (s.start + FLIGHT_MS) < 700);
    const shake = lastGoal ? Math.sin((ms - lastGoal.start) / 25) * 0.35 * (1 - (ms - lastGoal.start - FLIGHT_MS) / 700) : 0;
    drawNet(ctx, (x, y) => P(attacking, x, y), colors, shake);
  }

  // 已结束的射门:圆点(进球大一圈、金边)
  for (const s of shots) {
    if (ms < s.start + FLIGHT_MS) continue;
    const [sx, sy] = P(s.team, s.sd[0], s.sd[1]);
    const goal = s.sd[3] === RESULT_GOAL;
    ctx.globalAlpha = goal ? 1 : 0.85;
    ctx.beginPath();
    ctx.arc(sx, sy, goal ? 1.35 : 0.85, 0, TAU);
    ctx.fillStyle = colors.team[s.team];
    ctx.fill();
    ctx.lineWidth = goal ? 0.45 : 0.22;
    ctx.strokeStyle = goal ? colors.gold : colors.ball;
    ctx.stroke();
  }
  ctx.globalAlpha = 1;

  // 飞行中 / 收尾中的球
  let flying = 0;
  for (const s of shots) {
    const t = ms - s.start;
    if (t < 0 || t > FLIGHT_MS + AFTER_MS) continue;
    flying += 1;
    const res = s.sd[3];
    const from: [number, number] = [s.sd[0], s.sd[1]];
    const b = ballAt(from, s.end, res, t);
    const [fx, fy] = P(s.team, from[0], from[1]);

    // 门将:扑救时从球门中央扑向落点
    if (res === RESULT_SAVED && t > FLIGHT_MS * 0.35) {
      const k = Math.min(1, (t - FLIGHT_MS * 0.35) / (FLIGHT_MS * 0.65));
      const gy = 34 + (s.end.y - 34) * k;
      const [kx, ky] = P(s.team, PITCH_LENGTH - 0.6, gy);
      ctx.beginPath();
      ctx.arc(kx, ky, 1.05, 0, TAU);
      ctx.fillStyle = colors.team[1 - s.team];
      ctx.fill();
      ctx.lineWidth = 0.25;
      ctx.strokeStyle = colors.ball;
      ctx.stroke();
    }

    if (!b) continue;
    const [bx, by] = P(s.team, b.x, b.y);
    // 轨迹
    if (t <= FLIGHT_MS + 150) {
      ctx.setLineDash([0.8, 0.6]);
      ctx.lineWidth = 0.3;
      ctx.strokeStyle = colors.team[s.team];
      ctx.globalAlpha = 0.75;
      ctx.beginPath();
      ctx.moveTo(fx, fy);
      ctx.lineTo(bx, by);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    }
    // 落点效果
    if (t > FLIGHT_MS) {
      const u = (t - FLIGHT_MS) / AFTER_MS;
      const [ex, ey] = P(s.team, s.end.x, s.end.y);
      if (res === RESULT_BLOCKED || res === RESULT_POST || res === RESULT_GOAL) {
        ctx.globalAlpha = Math.max(0, 1 - u);
        ctx.beginPath();
        ctx.arc(ex, ey, 0.6 + 2.6 * u, 0, TAU);
        ctx.lineWidth = 0.35;
        ctx.strokeStyle = res === RESULT_GOAL ? colors.gold : colors.ink;
        ctx.stroke();
        ctx.globalAlpha = 1;
      }
    }
    // 影子 + 球(离地越高越大)
    ctx.globalAlpha = 0.28 * b.alpha;
    ctx.beginPath();
    ctx.ellipse(bx, by, 0.75, 0.5, 0, 0, TAU);
    ctx.fillStyle = colors.ink;
    ctx.fill();
    const lift = Math.min(b.z, 8) * 0.35;
    const [lx, ly] = o === "landscape" ? [bx, by - lift] : [bx + lift, by];
    ctx.globalAlpha = b.alpha;
    ctx.beginPath();
    ctx.arc(lx, ly, 0.75 + Math.min(b.z, 8) * 0.09, 0, TAU);
    ctx.fillStyle = colors.ball;
    ctx.fill();
    ctx.lineWidth = 0.22;
    ctx.strokeStyle = colors.ink;
    ctx.stroke();
    ctx.globalAlpha = 1;
  }
  return { flying };
}

function drawNet(ctx: CanvasRenderingContext2D, P: (x: number, y: number) => [number, number], colors: CanvasColors, shake: number) {
  const depth = Math.min(NET_DEPTH, NET_MARGIN - 0.3);
  const corners = [
    P(PITCH_LENGTH, GOAL_Y_MIN),
    P(PITCH_LENGTH + depth, GOAL_Y_MIN + 0.4 + shake),
    P(PITCH_LENGTH + depth, GOAL_Y_MAX - 0.4 + shake),
    P(PITCH_LENGTH, GOAL_Y_MAX),
  ];
  ctx.lineWidth = 0.18;
  ctx.strokeStyle = colors.net;
  ctx.globalAlpha = 0.8;
  ctx.beginPath();
  ctx.moveTo(corners[0][0], corners[0][1]);
  for (const c of corners.slice(1)) ctx.lineTo(c[0], c[1]);
  ctx.stroke();
  // 网格
  ctx.globalAlpha = 0.35;
  ctx.lineWidth = 0.1;
  for (let k = 1; k < 6; k++) {
    const y = GOAL_Y_MIN + ((GOAL_Y_MAX - GOAL_Y_MIN) * k) / 6;
    const a = P(PITCH_LENGTH, y);
    const b = P(PITCH_LENGTH + depth, y + shake);
    ctx.beginPath();
    ctx.moveTo(a[0], a[1]);
    ctx.lineTo(b[0], b[1]);
    ctx.stroke();
  }
  // 门柱
  ctx.globalAlpha = 1;
  ctx.fillStyle = colors.ink;
  for (const y of [GOAL_Y_MIN, GOAL_Y_MAX]) {
    const [x0, y0] = P(PITCH_LENGTH, y);
    ctx.beginPath();
    ctx.arc(x0, y0, 0.32, 0, TAU);
    ctx.fill();
  }
}
