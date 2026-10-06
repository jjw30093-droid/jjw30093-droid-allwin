// xG 走势图进球人名的摆放(纯函数,无 "use client";XgRaceChart 与测试共用)。
//
// 2026-09-30 定位的问题:原先主队人名固定放进球点上方、客队固定放下方。阶梯线在进球那一分钟竖直跳升,
// 进球点正是这段竖线的顶端,放在下方的客队人名 82% 压在自家竖线上;人名也不避让对方的线和其它人名。
// 300 场模拟(手机 311×220)实测对比几种摆法后选定:
//   左上优先 —— 进球点左边是跳升前那段较低的线,上方空着,所以左上永远碰不到自家的线;
//   左上被对方的线 / 其它人名 / 进球点占了,或出界,就改右下 —— 右边自家的线水平延续在进球点高度或更高,下方空着;
//   两处都不干净,取冲突少的一处(同样少取左上);每个候选都先推回绘图区内再评估(靠边的进球推回后可能压线)。
// 文字另加一圈与底色同色的描边(XgRaceChart 里设置),挤不开时仍能读清。

export type LabelSide = "UL" | "LR" | "UR" | "LL";

export interface LabelPoint {
  minute: number;
  total: number;
}

export interface GoalLabelInput extends LabelPoint {
  team: 0 | 1;
  name: string;
}

/** 绘图区像素几何:w/h = 绘图区宽高(不含坐标轴),xMax/yMax = 坐标轴上限,font = 人名字号,r = 进球点半径 */
export interface PlotGeometry {
  w: number;
  h: number;
  xMax: number;
  yMax: number;
  font: number;
  r: number;
}

export interface LabelPlacement {
  side: LabelSide;
  /** 相对进球点中心的锚点偏移(像素):左上时锚点 = 文字右下角,右下时 = 文字左上角 */
  ox: number;
  oy: number;
  /** 冲突情况(测试与诊断用) */
  conflicts: { ownLine: boolean; otherLine: boolean; label: boolean; outside: boolean };
}

interface Box {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}
type Seg = [number, number, number, number];

/** 文字宽度估算:中文 1 个字号,ASCII 0.6 个字号 */
export function textWidth(name: string, font: number): number {
  return [...name].reduce((w, ch) => w + (/[\u0000-\u007f]/.test(ch) ? 0.6 : 1) * font, 0);
}

const GAP = 1;
const LIFT = 2;
// 左边至少留 8px，避免靠前进球的人名跨过纵轴；右/上仍允许略微伸出绘图区。
const BOUNDS = { leftInset: 8, rightExtra: 14, topExtra: 12, bottomExtra: 0 };

function toPx(p: LabelPoint, g: PlotGeometry): [number, number] {
  return [(p.minute / g.xMax) * g.w, g.h - (p.total / g.yMax) * g.h];
}

/** 阶梯线(step:'end')的线段:相邻两点之间先水平走到下一点的横坐标,再竖直跳到下一点 */
export function stepSegments(points: LabelPoint[], g: PlotGeometry): Seg[] {
  const out: Seg[] = [];
  for (let i = 1; i < points.length; i++) {
    const [x0, y0] = toPx(points[i - 1], g);
    const [x1, y1] = toPx(points[i], g);
    out.push([x0, y0, x1, y0], [x1, y0, x1, y1]);
  }
  return out;
}

/** 锚点:左侧(UL/LL)= 文字右边缘,右侧(UR/LR)= 文字左边缘;上方(UL/UR)= 文字下边缘,下方(LR/LL)= 文字上边缘 */
function boxFor(side: LabelSide, px: number, py: number, w: number, g: PlotGeometry, lift = 0, push = 0): { box: Box; ox: number; oy: number } {
  const h = g.font + 2;
  const left = side === "UL" || side === "LL";
  const up = side === "UL" || side === "UR";
  const ox = left ? -g.r - GAP - push : g.r + GAP + push;
  const oy = up ? -LIFT - lift : LIFT + lift;
  const x0 = left ? px + ox - w : px + ox;
  const y0 = up ? py + oy - h : py + oy;
  return { box: { x0, x1: x0 + w, y0, y1: y0 + h }, ox, oy };
}

/** 候选顺序:左上 → 右下(站长定的主方案);两处都放不下时(几乎只发生在开场 / 贴底的进球)
 *  再试右上、左下，并逐级横移/上下错开；靠左的长名字优先换侧，避免压住纵轴。
 *  首个进球最多上下错开 24px，避免读图起点的名字离圆点太远。 */
const CANDIDATES: [LabelSide, number, number][] = [
  ["UL", 0, 0],
  ["LR", 0, 0],
  ["UR", 0, 0],
  ["LL", 0, 0],
  ["LR", 0, 12],
  ["UR", 0, 12],
  ["UL", 12, 0],
  ["UR", 12, 0],
  ["LR", 12, 0],
  ["LL", 12, 0],
  ["LR", 12, 12],
  ["UR", 12, 12],
  ["UL", 24, 0],
  ["UR", 24, 0],
  ["LR", 24, 0],
  ["LL", 24, 0],
  ["UL", 36, 0],
  ["UR", 36, 0],
  ["UL", 48, 0],
  ["UR", 48, 0],
];

function segHits(s: Seg, b: Box): boolean {
  const [ax, ay, bx, by] = s;
  if (Math.abs(ax - bx) < 1e-6) return ax > b.x0 && ax < b.x1 && Math.max(ay, by) > b.y0 && Math.min(ay, by) < b.y1;
  return ay > b.y0 && ay < b.y1 && Math.max(ax, bx) > b.x0 && Math.min(ax, bx) < b.x1;
}

const overlap = (a: Box, b: Box) => a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;

function outside(b: Box, g: PlotGeometry): boolean {
  return b.x0 < BOUNDS.leftInset || b.x1 > g.w + BOUNDS.rightExtra || b.y0 < -BOUNDS.topExtra || b.y1 > g.h + BOUNDS.bottomExtra;
}

/** 把框推回允许区域,返回需要的平移量 */
function clampShift(b: Box, g: PlotGeometry): [number, number] {
  let dx = 0;
  let dy = 0;
  if (b.x0 < BOUNDS.leftInset) dx = BOUNDS.leftInset - b.x0;
  else if (b.x1 > g.w + BOUNDS.rightExtra) dx = g.w + BOUNDS.rightExtra - b.x1;
  if (b.y0 < -BOUNDS.topExtra) dy = -BOUNDS.topExtra - b.y0;
  else if (b.y1 > g.h + BOUNDS.bottomExtra) dy = g.h + BOUNDS.bottomExtra - b.y1;
  return [dx, dy];
}

/**
 * 给每个进球人名选位置。goals 与 lines 用同一套数据坐标(分钟、累计 xG);
 * lines[t] = 第 t 队的阶梯线点列(含 0 分钟起点与终场延长点)。返回顺序与 goals 一致。
 */
export function placeGoalLabels(goals: GoalLabelInput[], lines: [LabelPoint[], LabelPoint[]], g: PlotGeometry): LabelPlacement[] {
  const segs: [Seg[], Seg[]] = [stepSegments(lines[0], g), stepSegments(lines[1], g)];
  const dots: Box[] = goals.map((p) => {
    const [x, y] = toPx(p, g);
    return { x0: x - g.r, x1: x + g.r, y0: y - g.r, y1: y + g.r };
  });
  const placed: Box[] = [];
  const result: LabelPlacement[] = new Array(goals.length);
  // 从左到右依次放,后放的避让先放的
  const order = goals.map((_, i) => i).sort((a, b) => goals[a].minute - goals[b].minute || a - b);
  for (const i of order) {
    const p = goals[i];
    const [px, py] = toPx(p, g);
    const w = textWidth(p.name, g.font);
    const check = (b: Box) => ({
      ownLine: segs[p.team].some((s) => segHits(s, b)),
      otherLine: segs[1 - p.team].some((s) => segHits(s, b)),
      label: placed.some((o) => overlap(o, b)) || dots.some((d, j) => j !== i && overlap(d, b)),
      outside: outside(b, g),
    });
    const score = (c: ReturnType<typeof check>) => +c.ownLine * 4 + +c.otherLine * 2 + +c.label * 3 + +c.outside;
    // 每个候选先推回允许区域再评估:靠边的进球(开场、终场、最高点)被推回后可能压到自家竖线,
    // 这时另一侧往往是干净的(例如开场的进球,左上被推回右移会压线,右下天然在界内)
    const candidates = i === order[0] ? CANDIDATES.filter(([, lift]) => lift <= 24) : CANDIDATES;
    const cands = candidates.map(([side, lift, push]) => {
      const f = boxFor(side, px, py, w, g, lift, push);
      const [dx, dy] = clampShift(f.box, g);
      const box = { x0: f.box.x0 + dx, x1: f.box.x1 + dx, y0: f.box.y0 + dy, y1: f.box.y1 + dy };
      // 横向推回会让左侧文字靠近进球点和竖直跳线；同样干净时优先选天然在界内的右侧候选。
      return {
        side,
        box,
        ox: f.ox + dx,
        oy: f.oy + dy,
        c: check(box),
        clampPenalty: Math.abs(dx) > 0.01 ? 1 : 0,
        // 首个进球是读图起点，优先贴近圆点；后续标签仍以完全避让曲线为先。
        distancePenalty: i === order[0] ? (lift + push) / 12 : 0,
      };
    });
    // 取第一个完全干净的候选;都不干净时取冲突最少的(同样少取靠前的)
    const candidateScore = (x: (typeof cands)[number]) => score(x.c) + x.clampPenalty + x.distancePenalty;
    const best = cands.find((x) => candidateScore(x) === 0) ?? cands.reduce((a, b) => (candidateScore(b) < candidateScore(a) ? b : a));
    placed.push(best.box);
    result[i] = { side: best.side, ox: best.ox, oy: best.oy, conflicts: best.c };
  }
  return result;
}

/** 纵轴上限与刻度间隔:留出最高点上方放人名的空间,取整到好读的刻度 */
export function niceYAxis(maxTotal: number, plotH: number, font: number): { max: number; interval: number } {
  const headroom = (font + 2 + LIFT + 2) / Math.max(plotH, 1);
  const need = Math.max(0.3, maxTotal / Math.max(0.5, 1 - headroom));
  // 刻度文字只保留 1 位小数(XgRaceChart 的 axisLabel),所以间隔只能是 1 位小数能准确表示的值——0.25 会显示成 0.3 / 1.3
  for (const interval of [0.1, 0.2, 0.3, 0.5, 1, 2, 5]) {
    const max = Math.ceil(need / interval - 1e-9) * interval;
    if (max / interval <= 6) return { max: Math.round(max * 1000) / 1000, interval };
  }
  return { max: Math.ceil(need), interval: Math.ceil(need / 5) };
}
