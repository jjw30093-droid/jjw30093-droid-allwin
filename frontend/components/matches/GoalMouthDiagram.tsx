/**
 * 球门框示意图(2026-08-25 首版;2026-08-26 按 FotMob 官方安卓包反编译出的
 * 渲染公式重做主路径):正面看球门,标出该次射门越过球门线时的位置。
 *
 * 纯内联 SVG,不用 ECharts——复用 FootballPitchBackground.tsx 的既有理由:
 * 内联 SVG 是 DOM 元素,天然支持 CSS 自定义属性,var() 走级联随主题切换,
 * 深浅色零成本、同一份 JSX(CLAUDE.md §11.2);且不触发 §11.3 的
 * buildOption + headless 冒烟义务(那条纪律只约束"构造 ECharts option 的
 * 组件")。放 components/matches/(比赛专用球场类视觉件),不放 charts/
 * (那里是图表基础设施)。
 *
 * 三条渲染路径(优先级从高到低):
 *  1. blocked(is_blocked===true)→ 静态球门 + "被封堵"说明,**不画标记**
 *     ——FotMob 反编译实证:isBlocked 时整个入网标记隐藏(被封堵的球没到
 *     球门线,画一个"入网位置"是撒谎)。
 *  2. onGoalShot 主路径(FotMob 公式,反编译逐字节实证,不是猜测):
 *     固定画布宽 W、高 H=W/3(球门画 3:1);球门画幅按 zoomRatio 缩放、
 *     锚定画布**底边中点**;标记点 left=(x/2)·W、bottom=(y/0.68)·H,
 *     **始终对固定画布度量,不对缩放后的画幅度量**。zoomRatio=1 时两套
 *     坐标系重合(画幅铺满画布);偏出很远的 Miss(生产实测 zoomRatio
 *     0.24~0.79、x 钉在 0 或 2)画幅缩小、标记落在画布边缘——视觉上
 *     "明显偏出(变小的)球门",画布不缩、不裁剪、不硬塞进框里。
 *  3. goal_crossed_y/z 旧路径(2026-08-25 版,坐标语义已对生产验证):
 *     on_goal_shot 未采集的历史场次(2020-2025 大部分)继续用它,不丢失
 *     既有能力。两者都没有 → 诚实空态文案。
 *
 * 视角:**始终按射手视角画**(x/gx 0=左门柱侧),不套 ShotMapChart 的
 * mirrorPoint——那是球场俯视图的展示变换,球门框正视图没有这个概念。
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

/** FotMob onGoalShot 原始域(APK 反编译 + 生产数据实证):
 * x ∈ [0, 2](射手视角,0=左门柱、2=右门柱、1=球门中心;偏出的 Miss 被
 * 来源钉在 ~0 或 2),y ∈ [0, 0.68](0=地面),zoomRatio ∈ (0, 1]
 * (null → 1.0;<1 表示球门画幅按比例缩小,用于表达"偏出很远")。 */
export interface OnGoalShotPoint {
  x: number;
  y: number;
  zoomRatio: number;
}

const ON_GOAL_X_MAX = 2.0;
const ON_GOAL_Y_MAX = 0.68;
/** 域边界容差:来源有 -2.2e-16 级浮点噪声;显著出域(x=2.5、y=0.9 这种)
 * 不是噪声而是没见过的数据形状,拒绝(→ null → 诚实空态),不裁剪。 */
const ON_GOAL_EPS = 0.05;

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

/** on_goal_shot_x/y/zoom_ratio → 校验后的渲染输入;域外/非法 → null
 * (CLAUDE.md §6.2:不静默填 0,由调用方走旧路径或空态)。
 * zoomRatio 为 null 时按 FotMob 客户端行为取 1.0(这是反编译实证的默认值,
 * 不是本站猜测的兜底)。 */
export function normalizeOnGoalShot(
  x: number | null | undefined,
  y: number | null | undefined,
  zoomRatio: number | null | undefined,
): OnGoalShotPoint | null {
  if (x == null || y == null) return null;
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  if (x < -ON_GOAL_EPS || x > ON_GOAL_X_MAX + ON_GOAL_EPS) return null;
  if (y < -ON_GOAL_EPS || y > ON_GOAL_Y_MAX + ON_GOAL_EPS) return null;
  const z = zoomRatio ?? 1.0;
  if (!Number.isFinite(z)) return null;
  if (z <= 0 || z > 1 + ON_GOAL_EPS) return null;
  return {
    x: clamp(x, 0, ON_GOAL_X_MAX),
    y: clamp(y, 0, ON_GOAL_Y_MAX),
    zoomRatio: Math.min(z, 1),
  };
}

/** 米数短语(旧路径口径,坐标语义已验证——见 normalizeGoalMouthPoint)。
 * 框外时说"偏出左/右门柱约 X 米"而不是打印负数米。 */
function metersPhrase(point: GoalMouthPoint): string {
  const hx = point.gx * GOAL_WIDTH_M;
  const hz = point.gz * GOAL_HEIGHT_M;
  const horiz =
    hx < 0
      ? `偏出左门柱约 ${(-hx).toFixed(2)} 米`
      : hx > GOAL_WIDTH_M
        ? `偏出右门柱约 ${(hx - GOAL_WIDTH_M).toFixed(2)} 米`
        : `距左门柱 ${hx.toFixed(2)} 米`;
  const vert =
    hz > GOAL_HEIGHT_M
      ? `高出横梁约 ${(hz - GOAL_HEIGHT_M).toFixed(2)} 米`
      : `离地 ${hz.toFixed(2)} 米`;
  return `${horiz}、${vert}`;
}

/** §11.2 文字摘要(旧路径)的唯一出口(纯函数,测试直接断言)。 */
export function buildGoalMouthSummary(point: GoalMouthPoint): string {
  return `球门线穿越位置(射手视角):${metersPhrase(point)}(球门宽 7.32 米、高 2.44 米)`;
}

/** §11.2 文字摘要(onGoalShot 主路径)。[0,2] 横向域到米的换算**没有**被
 * 反编译证实(只有渲染公式被证实),所以这里绝不编一个米数出来——位置只做
 * 定性描述(框内偏左/中路/偏右 × 贴地/半高/近横梁,或偏出/高出);同场若有
 * 已验证米数口径的 goal_crossed_y/z,以括注补充精确米数(两个来源,分开
 * 措辞,不混称)。诚实的定性 > 编造的精确(CLAUDE.md §2.2)。
 *
 * "框内/偏出"的判定与渲染几何完全同源:画幅横跨数据域 x ∈ [1-z, 1+z]、
 * 纵向 y ≤ 0.68·z 在横梁下——文字和图必须说同一件事(§11.3 图例纪律的
 * 同一精神)。 */
export function buildOnGoalShotSummary(
  p: OnGoalShotPoint,
  legacy?: GoalMouthPoint | null,
): string {
  const z = p.zoomRatio;
  const eps = 1e-9;
  const leftOfPost = p.x < 1 - z - eps;
  const rightOfPost = p.x > 1 + z + eps;
  const overBar = p.y > ON_GOAL_Y_MAX * z + eps;
  let pos: string;
  if (leftOfPost || rightOfPost || overBar) {
    const parts: string[] = [];
    if (leftOfPost) parts.push("偏出左门柱外");
    else if (rightOfPost) parts.push("偏出右门柱外");
    if (overBar) parts.push("高出横梁");
    pos = parts.join("且");
  } else {
    const tx = (p.x - (1 - z)) / (2 * z);
    const ty = p.y / (ON_GOAL_Y_MAX * z);
    const horiz = tx < 1 / 3 ? "偏左" : tx > 2 / 3 ? "偏右" : "中路";
    const vert = ty < 0.33 ? "贴地" : ty < 0.7 ? "半高" : "近横梁高度";
    pos = `框内${horiz}、${vert}`;
  }
  const base = `入网位置(射手视角):${pos}`;
  if (!legacy) return base;
  return `${base}(球门线口径:${metersPhrase(legacy)})`;
}

/** 被封堵射门的说明文案(FotMob 行为:isBlocked 时隐藏入网标记)。 */
export const BLOCKED_GOAL_MOUTH_TEXT = "射门被封堵,未到达球门线。";

/* ── onGoalShot 主路径的画布几何(FotMob 公式) ──────────────────────
 * 固定画布:宽 CANVAS_W、高 CANVAS_H = CANVAS_W/3(取 W=7.32 使 zoom=1 时
 * 画布坐标恰好等于"米"——纯粹是单位选择,公式本身与单位无关)。
 * 球门画幅:宽 W·z、高 (W·z)/3,锚定画布底边中点 → 画幅横跨
 * [W(1-z)/2, W(1+z)/2],顶边在 H(1-z)。
 * 标记:cx=(x/2)·W、cy=H-(y/0.68)·H——对固定画布度量,永不随 z 缩放。 */
const CANVAS_W = GOAL_WIDTH_M;
const CANVAS_H = GOAL_WIDTH_M / 3; // = 2.44,球门画 3:1
const ZVIEW_X0 = -0.5;
const ZVIEW_Y0 = -0.45;
const ZVIEW_W = CANVAS_W + 1.0;
const ZVIEW_H = CANVAS_H + 0.91; // 地面(y=CANVAS_H)以下留 0.46 呼吸空间

/** 缩放画幅内的球网斜线格(纯装饰,低对比,aria-hidden):网格随画幅一起
 * 缩放(格距 0.6·z),不是画在固定画布上。 */
function netPathScaled(z: number): string {
  const fx0 = (CANVAS_W * (1 - z)) / 2;
  const fx1 = CANVAS_W - fx0;
  const fy0 = CANVAS_H * (1 - z);
  const parts: string[] = [];
  for (let x = 0.6; x < GOAL_WIDTH_M; x += 0.6) {
    parts.push(`M ${(fx0 + x * z).toFixed(3)} ${fy0.toFixed(3)} V ${CANVAS_H}`);
  }
  for (let y = 0.6; y < GOAL_HEIGHT_M; y += 0.6) {
    parts.push(`M ${fx0.toFixed(3)} ${(fy0 + y * z).toFixed(3)} H ${fx1.toFixed(3)}`);
  }
  return parts.join(" ");
}

/** onGoalShot 主路径与"被封堵"分支共用的球门画幅渲染。 */
function ZoomedGoalFigure({
  zoomRatio,
  marker,
  color,
  caption,
}: {
  zoomRatio: number;
  /** null = 不画标记(被封堵分支)。坐标已经是固定画布坐标。 */
  marker: { cx: number; cy: number } | null;
  color: string;
  caption: string;
}): JSX.Element {
  const z = zoomRatio;
  const fx0 = (CANVAS_W * (1 - z)) / 2;
  const fx1 = CANVAS_W - fx0;
  const fy0 = CANVAS_H * (1 - z);
  return (
    <figure className={styles.wrap}>
      <svg
        viewBox={`${ZVIEW_X0} ${ZVIEW_Y0} ${ZVIEW_W} ${ZVIEW_H}`}
        className={styles.svg}
        role="img"
        aria-label={caption}
      >
        {/* 球网(装饰,随画幅缩放) */}
        <path d={netPathScaled(z)} aria-hidden className={styles.net} fill="none" />
        {/* 地面线(固定画布,不随画幅缩放——球门是缩了,地平线没缩) */}
        <line
          x1={ZVIEW_X0}
          y1={CANVAS_H}
          x2={ZVIEW_X0 + ZVIEW_W}
          y2={CANVAS_H}
          className={styles.ground}
        />
        {/* 门框:左柱 + 横梁 + 右柱,按 zoomRatio 缩放、锚定底边中点。
            描边宽度保持固定(不随 z 缩细到不可见)。 */}
        <path
          d={`M ${fx0} ${CANVAS_H} V ${fy0} H ${fx1} V ${CANVAS_H}`}
          className={styles.frame}
          fill="none"
          data-goal-frame
        />
        {/* 标记:对固定画布定位(FotMob 公式核心——不跟画幅走);球队色
            实心圆 + var(--ink) 细描边,对比度断言见
            frontend/tests/goal-mouth-diagram.test.tsx(背景是面板底色
            --surface,不是球场底色) */}
        {marker && (
          <circle
            cx={marker.cx}
            cy={marker.cy}
            r={0.3}
            fill={color}
            stroke="var(--ink)"
            strokeWidth={0.06}
          />
        )}
      </svg>
      <figcaption className={styles.caption}>{caption}</figcaption>
    </figure>
  );
}

/* 旧路径 viewBox 留白版:球门 7.32×2.44m + 门柱厚度 + 框外空间。
 * x ∈ [-0.9, 8.22],y ∈ [-0.5, 2.94](SVG y 向下,y=0 是横梁上沿高度、
 * y=GOAL_HEIGHT_M 是地面)。 */
const VIEW_X0 = -0.9;
const VIEW_Y0 = -0.5;
const VIEW_W = 9.12;
const VIEW_H = 3.44;

/** 旧路径球网斜线格(纯装饰,低对比,aria-hidden,不受 3:1 约束)。 */
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
  onGoal = null,
  blocked = false,
  color,
  summary,
  outsideFrame = false,
}: {
  /** 旧路径输入(goal_crossed_y/z 归一化结果),onGoal 为 null 时才用。 */
  point: GoalMouthPoint | null;
  /** FotMob onGoalShot 主路径输入(normalizeOnGoalShot 归一化结果)。 */
  onGoal?: OnGoalShotPoint | null;
  /** is_blocked===true 时隐藏标记(FotMob 行为),画静态球门 + 说明。 */
  blocked?: boolean;
  /** 标记色:调用方传已经过 resolveMatchColors 的球队色。 */
  color: string;
  /** §11.2 文字摘要,同时作 aria-label。 */
  summary: string;
  /** 旧路径:结果是 Miss/Post 时允许画在框外(viewBox 留白范围内),默认 false。 */
  outsideFrame?: boolean;
}): JSX.Element | null {
  if (blocked) {
    return (
      <ZoomedGoalFigure
        zoomRatio={1}
        marker={null}
        color={color}
        caption={BLOCKED_GOAL_MOUTH_TEXT}
      />
    );
  }
  if (onGoal) {
    return (
      <ZoomedGoalFigure
        zoomRatio={onGoal.zoomRatio}
        marker={{
          cx: (onGoal.x / ON_GOAL_X_MAX) * CANVAS_W,
          cy: CANVAS_H - (onGoal.y / ON_GOAL_Y_MAX) * CANVAS_H,
        }}
        color={color}
        caption={summary}
      />
    );
  }
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
          data-goal-frame
        />
        {/* 穿越点:球队色实心圆 + var(--ink) 细描边(对比度断言见
            frontend/tests/goal-mouth-diagram.test.tsx,背景是面板底色
            --surface,不是球场底色——不能复用射门图那份 fixture 数字) */}
        <circle cx={hx} cy={sy} r={0.3} fill={color} stroke="var(--ink)" strokeWidth={0.06} />
      </svg>
      <figcaption className={styles.caption}>{summary}</figcaption>
    </figure>
  );
}
