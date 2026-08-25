/**
 * 球门框示意图(2026-08-25,射门详情面板内嵌):正面看球门,标出该次射门
 * 越过球门线时的横向位置与离地高度。
 *
 * 纯内联 SVG,不用 ECharts——复用 FootballPitchBackground.tsx 的既有理由:
 * 内联 SVG 是 DOM 元素,天然支持 CSS 自定义属性,var() 走级联随主题切换,
 * 深浅色零成本、同一份 JSX(CLAUDE.md §11.2);且不触发 §11.3 的
 * buildOption + headless 冒烟义务(那条纪律只约束"构造 ECharts option 的
 * 组件")。放 components/matches/(比赛专用球场类视觉件),不放 charts/
 * (那里是图表基础设施)。
 *
 * 视角:**始终按射手视角画**(gx 0=左门柱、1=右门柱),不套 ShotMapChart
 * 的 mirrorPoint——那是球场俯视图的展示变换,球门框正视图没有这个概念。
 */

import type { JSX } from "react";
import styles from "./GoalMouthDiagram.module.css";

/** 球门框归一化坐标:gx 0=左门柱 1=右门柱(射手视角),gz 0=地面 1=横梁。 */
export interface GoalMouthPoint {
  gx: number;
  gz: number;
}

export const GOAL_WIDTH_M = 7.32;
export const GOAL_HEIGHT_M = 2.44;

/** 球门左门柱对应的球场 Y 坐标(球门中心 y=34,跨 34±3.66;与
 * FootballPitchBackground.tsx 的 GOAL_Y0/GOAL_Y1 同一口径)。 */
const GOAL_Y0 = 30.34;

/** goal_crossed_y/z → 球门框归一化坐标。
 *
 * 坐标语义 2026-08-25 已对生产 fact_shotmap 真实数值验证(不再是未验证
 * 假设):goal_crossed_y 是球场 Y 坐标——射正结果(Goal n=22 /
 * AttemptSaved n=104)全部落在球门跨度 [31.26, 37.66] 内(球门 30.34..
 * 37.66),Post(n=5)落在框沿 [30.27, 37.74],Miss(n=79)散布
 * [17.41, 62.16] 即框外;goal_crossed_z 是离地高度(米)——射正样本
 * ∈ [0.04, 2.2](横梁 2.44),Miss 可达 7.6。
 *
 * 落在合理域外(crossedY ∉ [0,68] 球场宽度域、crossedZ ∉ [0,10])一律
 * 返回 null——不裁剪、不补 0(CLAUDE.md §6.2:缺失值不得静默填 0);
 * 输入为 null/undefined 同样返回 null,由调用方渲染诚实的空态文案。
 */
export function normalizeGoalMouthPoint(
  crossedY: number | null | undefined,
  crossedZ: number | null | undefined,
): GoalMouthPoint | null {
  if (crossedY == null || crossedZ == null) return null;
  if (!Number.isFinite(crossedY) || !Number.isFinite(crossedZ)) return null;
  if (crossedY < 0 || crossedY > 68) return null;
  if (crossedZ < 0 || crossedZ > 10) return null;
  return {
    gx: (crossedY - GOAL_Y0) / GOAL_WIDTH_M,
    gz: crossedZ / GOAL_HEIGHT_M,
  };
}

/** §11.2 文字摘要的唯一出口(纯函数,测试直接断言)。 */
export function buildGoalMouthSummary(point: GoalMouthPoint): string {
  const hx = point.gx * GOAL_WIDTH_M;
  const hz = point.gz * GOAL_HEIGHT_M;
  return (
    `球门线穿越位置(射手视角):距左门柱 ${hx.toFixed(2)} 米、` +
    `离地 ${hz.toFixed(2)} 米(球门宽 7.32 米、高 2.44 米)`
  );
}

/* viewBox 留白版:球门 7.32×2.44m + 门柱厚度 + 框外空间。
 * x ∈ [-0.9, 8.22],y ∈ [-0.5, 2.94](SVG y 向下,y=0 是横梁上沿高度、
 * y=GOAL_HEIGHT_M 是地面)。 */
const VIEW_X0 = -0.9;
const VIEW_Y0 = -0.5;
const VIEW_W = 9.12;
const VIEW_H = 3.44;

/** 球网斜线格(纯装饰,低对比,aria-hidden,不受 3:1 约束)。 */
function netPath(): string {
  const parts: string[] = [];
  for (let x = 0.6; x < GOAL_WIDTH_M; x += 0.6) {
    parts.push(`M ${x.toFixed(2)} 0 V ${GOAL_HEIGHT_M}`);
  }
  for (let y = 0.6; y < GOAL_HEIGHT_M; y += 0.6) {
    parts.push(`M 0 ${y.toFixed(2)} H ${GOAL_WIDTH_M}`);
  }
  return parts.join(" ");
}

export function GoalMouthDiagram({
  point,
  color,
  summary,
  outsideFrame = false,
}: {
  point: GoalMouthPoint | null;
  /** 标记色:调用方传已经过 resolveMatchColors 的球队色。 */
  color: string;
  /** §11.2 文字摘要,同时作 aria-label。 */
  summary: string;
  /** 结果是 Miss/Post 时允许画在框外(viewBox 留白范围内),默认 false。 */
  outsideFrame?: boolean;
}): JSX.Element | null {
  if (!point) {
    return <p className={styles.fallback}>该次射门没有可靠的入网位置数据。</p>;
  }
  const hx = point.gx * GOAL_WIDTH_M;
  const sy = GOAL_HEIGHT_M - point.gz * GOAL_HEIGHT_M;
  const inViewBox =
    hx >= VIEW_X0 && hx <= VIEW_X0 + VIEW_W && sy >= VIEW_Y0 && sy <= VIEW_Y0 + VIEW_H;
  const insideFrame = point.gx >= 0 && point.gx <= 1 && point.gz >= 0 && point.gz <= 1;
  // 画不下(远离球门的 Miss)或与结果语义矛盾(声称射正却落在框外)时,
  // 与缺数据同一空态——不裁剪进框里假装精确。
  if (!inViewBox || (!insideFrame && !outsideFrame)) {
    return <p className={styles.fallback}>该次射门没有可靠的入网位置数据。</p>;
  }
  return (
    <figure className={styles.wrap}>
      <svg
        viewBox={`${VIEW_X0} ${VIEW_Y0} ${VIEW_W} ${VIEW_H}`}
        className={styles.svg}
        role="img"
        aria-label={summary}
      >
        {/* 球网(装饰) */}
        <path d={netPath()} aria-hidden className={styles.net} fill="none" />
        {/* 地面线 */}
        <line
          x1={VIEW_X0}
          y1={GOAL_HEIGHT_M}
          x2={VIEW_X0 + VIEW_W}
          y2={GOAL_HEIGHT_M}
          className={styles.ground}
        />
        {/* 门框:左柱 + 横梁 + 右柱(一笔画,--ink-2 高对比) */}
        <path
          d={`M 0 ${GOAL_HEIGHT_M} V 0 H ${GOAL_WIDTH_M} V ${GOAL_HEIGHT_M}`}
          className={styles.frame}
          fill="none"
        />
        {/* 穿越点:球队色实心圆 + var(--ink) 细描边(对比度断言见
            frontend/tests/goal-mouth-diagram.test.ts,背景是面板底色
            --surface,不是球场底色——不能复用射门图那份 fixture 数字) */}
        <circle cx={hx} cy={sy} r={0.3} fill={color} stroke="var(--ink)" strokeWidth={0.06} />
      </svg>
      <figcaption className={styles.caption}>{summary}</figcaption>
    </figure>
  );
}
