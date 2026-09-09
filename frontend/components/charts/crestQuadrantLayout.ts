/**
 * 队徽象限图的纯布局数学(2026-09-09)。无 React、无 DOM、无 echarts import,
 * 联赛球队数据页(league/TeamQuadrantChart)与比赛详情页
 * (matches/TeamStyleQuadrant)两张象限图共用。
 *
 * 为什么不用 chart.convertToPixel():
 * - 全站唯一图表封装 components/EChart.tsx 不暴露实例。走 convertToPixel 就得
 *   改封装,并接受"读像素 → 写 symbolOffset → 触发 notMerge 重设 → 再触发 effect"
 *   的反馈环,只能靠相等性守卫收敛,10 个图表调用点共担这个风险;
 * - jsdom 没有布局,convertToPixel 在测试里返回垃圾,本次最复杂的新逻辑(避让)
 *   会完全无法单测——CLAUDE.md §11.3 的存在就是为了禁止这种情况。
 *
 * 替代方案(已用项目自带 echarts 6 headless SSR 实测):显式 min/max +
 * 固定像素 grid 时,ECharts 的像素映射就是一条可精确复算的线性变换
 * (grid{left:46,right:26}、W=400、x∈[0,10] 时点 (5,2.5) 落在 x=210,
 * 46+0.5*(400-46-26)=210 完全吻合;y 轴 inverse 同样精确)。
 * symbolOffset 只移动符号与其标签,不影响坐标轴范围、markLine、命中测试——
 * 所以轴范围只由数据算一次、永不因布局变化,单趟布局必然够。
 *
 * 与 quadrantOption.ts 的契约:两边必须用同一份 grid(本文件的 QUADRANT_GRID
 * 或调用方传入的同一个对象),漂一个像素,布局算出来的位置就和 ECharts 画的
 * 位置对不上,徽章整体偏移。像素一致性由
 * frontend/tests/team-quadrant-layout.test.ts 的 SSR 渲染比对守着。
 */

export type AxisRange = { min: number; max: number; interval: number };
export type Grid = { left: number; right: number; top: number; bottom: number };
export type PlotBox = { width: number; height: number; grid: Grid };

/** top 34:给"选中队的队名标签永远显示"留出上方空间(旧值 26)。 */
export const QUADRANT_GRID: Grid = { left: 46, right: 26, top: 34, bottom: 44 };

export const CREST = {
  /** 队徽边长下限/上限(CSS px)。18 以下手机上认不出徽章细节;34 以上 20 队塞不下。 */
  MIN: 18,
  MAX: 34,
  /** 徽章之间的最小空隙(避让半径 = 边长/2 + PAD) */
  PAD: 4,
  /** 徽章总面积占绘图区面积的目标比例,按面积而不是按宽度算尺寸,任何纵横比都能优雅降级 */
  FILL_TARGET: 0.16,
  /** 避让位移上限 = 该比例 × 徽章边长;超过就宁可重叠也不再推,避免"画到别的象限去" */
  MAX_SHIFT_RATIO: 0.9,
  /** 位移超过这个像素数就画引线 + 真实坐标小圆点,不让用户把徽章中心当成真实位置 */
  LEADER_MIN_SHIFT: 12,
  ITERATIONS: 60,
  DAMPING: 0.5,
  /**
   * 未选中队徽的透明度下限。象限色(win/loss/teal)在 0.35–0.45 透明度下压在
   * --surface 上合成对比度只有 1.4–2.2:1,低于 §11.3 的 3:1 红线——所以"调暗"
   * 只能作用在图片队徽上(图片本身不承载单一色值语义),且不能低于 0.55。
   * 见 frontend/tests/team-quadrant-contrast.test.ts。
   */
  DIM_OPACITY: 0.55,
  /** 无选中时的基础透明度(维持改造前 0.9 的观感) */
  BASE_OPACITY: 0.9,
  SELECTED_SCALE: 1.25,
  /** 手机点击目标上限;实际值取避让后达成的最小间距(见 hitSizeFor) */
  HIT_MAX: 44,
} as const;

function decimalsOf(step: number): number {
  const s = step.toFixed(10).replace(/0+$/, "");
  const dot = s.indexOf(".");
  return dot < 0 ? 0 : s.length - dot - 1;
}

/** {1, 2, 2.5, 5, 10} × 10^k 里不小于 raw 的最小值。 */
function niceStep(raw: number): number {
  if (!(raw > 0) || !Number.isFinite(raw)) return 1;
  const exp = 10 ** Math.floor(Math.log10(raw));
  const f = raw / exp;
  const pick = [1, 2, 2.5, 5, 10].find((c) => c >= f - 1e-9) ?? 10;
  const step = pick * exp;
  return Number(step.toFixed(decimalsOf(step) + 2));
}

/**
 * 数据范围 → 干净的显式端点 + 刻度间隔。
 *
 * 替代此前的 scale:true + boundaryGap 百分比:那套写法存在的理由是"不硬写
 * min/max,否则 ECharts 会把 0.47236656243863856 这种原始端点值直接画成刻度
 * 标签"——这里自己把端点吸附到 nice 刻度上,同时满足刻度干净与像素可复算。
 * 不强行把 0 拉进范围(scale:true 也不会),否则半张图是空白。
 */
export function niceAxisRange(
  values: number[],
  opts: { pad?: number; targetTicks?: number } = {},
): AxisRange {
  const pad = opts.pad ?? 0.14;
  const targetTicks = opts.targetTicks ?? 5;
  const finite = values.filter((v) => Number.isFinite(v));
  if (!finite.length) return { min: 0, max: 1, interval: 0.2 };
  const lo0 = Math.min(...finite);
  const hi0 = Math.max(...finite);
  // 全等输入:span=0 会让 step 变成 0/NaN,用数值本身的量级撑开一个可视区间
  const span = hi0 - lo0 > 0 ? hi0 - lo0 : Math.abs(hi0) || 1;
  const lo = lo0 - pad * span;
  const hi = hi0 + pad * span;
  const step = niceStep((hi - lo) / targetTicks);
  const d = decimalsOf(step);
  const min = Number((Math.floor(lo / step) * step).toFixed(d));
  const max = Number((Math.ceil(hi / step) * step).toFixed(d));
  return { min, max: max > min ? max : Number((min + step).toFixed(d)), interval: step };
}

/** 与 ECharts 自身的 value 轴映射逐像素一致(y 轴 inverse 时 min 在顶部)。 */
export function toPixel(
  p: { x: number; y: number },
  box: PlotBox,
  xr: AxisRange,
  yr: AxisRange,
  yInverse: boolean,
): { px: number; py: number } {
  const { left, right, top, bottom } = box.grid;
  const plotW = box.width - left - right;
  const plotH = box.height - top - bottom;
  const tx = (p.x - xr.min) / (xr.max - xr.min);
  const ty = (p.y - yr.min) / (yr.max - yr.min);
  return {
    px: left + tx * plotW,
    py: yInverse ? top + ty * plotH : top + (1 - ty) * plotH,
  };
}

/** 按绘图区面积与球队数定徽章边长;360px 卡片 20 队 ≈ 25px,900px 桌面卡片封顶 34px。 */
export function crestSizeFor(box: PlotBox, n: number): number {
  const { left, right, top, bottom } = box.grid;
  const plotW = Math.max(1, box.width - left - right);
  const plotH = Math.max(1, box.height - top - bottom);
  const raw = Math.sqrt((CREST.FILL_TARGET * plotW * plotH) / Math.max(1, n)) - CREST.PAD;
  return Math.round(Math.min(CREST.MAX, Math.max(CREST.MIN, raw)));
}

export type CrestLayout = {
  /** 真实数据位置(像素) */
  base: { px: number; py: number }[];
  /** 传给 ECharts symbolOffset 的绘制偏移;绝不改动 value */
  offset: [number, number][];
  /** 位移是否超过 LEADER_MIN_SHIFT——2026-09-09 起不再据此画引线/圆点(那本身
   *  就是"点",与"坐标点全部用队徽"的要求矛盾,见 quadrantOption.ts 头部说明),
   *  字段保留作为"位移是否明显"的度量,供测试与未来可能的调试用途。 */
  leader: boolean[];
  /** 避让后实际达成的最小中心距(<2 点时 Infinity),用于定命中层直径 */
  minSpacing: number;
};

/**
 * 确定性圆形排斥:固定轮数、按输入顺序遍历点对、无随机数——同输入同输出。
 * 完全重合的点(两队数据一模一样是真实存在的)用黄金角按下标错开,不产生 NaN。
 * 每轮做径向钳制(|pos-base| ≤ maxShift)并夹进绘图区内缩 r 的范围,
 * 徽章不会盖住轴标签。
 */
export function layoutCrests(input: {
  pts: { x: number; y: number }[];
  box: PlotBox;
  xr: AxisRange;
  yr: AxisRange;
  yInverse: boolean;
  radius: number;
  iterations?: number;
  damping?: number;
  maxShift?: number;
}): CrestLayout {
  const { pts, box, xr, yr, yInverse, radius } = input;
  const iterations = input.iterations ?? CREST.ITERATIONS;
  const damping = input.damping ?? CREST.DAMPING;
  const maxShift = input.maxShift ?? CREST.MAX_SHIFT_RATIO * radius * 2;
  const base = pts.map((p) => toPixel(p, box, xr, yr, yInverse));
  const pos = base.map((b) => ({ px: b.px, py: b.py }));
  const n = pos.length;
  const minDist = radius * 2;

  const { left, right, top, bottom } = box.grid;
  const xLo = left + radius;
  const xHi = box.width - right - radius;
  const yLo = top + radius;
  const yHi = box.height - bottom - radius;
  const clampAxis = (v: number, lo: number, hi: number) =>
    lo <= hi ? Math.max(lo, Math.min(hi, v)) : (lo + hi) / 2;

  for (let iter = 0; iter < iterations; iter++) {
    let moved = false;
    for (let j = 0; j < n; j++) {
      for (let k = j + 1; k < n; k++) {
        const dx = pos[k].px - pos[j].px;
        const dy = pos[k].py - pos[j].py;
        const d = Math.hypot(dx, dy);
        if (d >= minDist) continue;
        moved = true;
        let ux: number;
        let uy: number;
        if (d < 1e-6) {
          const angle = j * 2.39996;
          ux = Math.cos(angle);
          uy = Math.sin(angle);
        } else {
          ux = dx / d;
          uy = dy / d;
        }
        const push = ((minDist - d) / 2) * damping;
        pos[j].px -= ux * push;
        pos[j].py -= uy * push;
        pos[k].px += ux * push;
        pos[k].py += uy * push;
      }
    }
    for (let i = 0; i < n; i++) {
      let ox = pos[i].px - base[i].px;
      let oy = pos[i].py - base[i].py;
      const len = Math.hypot(ox, oy);
      if (len > maxShift) {
        ox *= maxShift / len;
        oy *= maxShift / len;
      }
      pos[i].px = clampAxis(base[i].px + ox, xLo, xHi);
      pos[i].py = clampAxis(base[i].py + oy, yLo, yHi);
    }
    if (!moved) break;
  }

  const round = (v: number) => Math.round(v * 2) / 2;
  const offset = pos.map((p, i): [number, number] => [
    round(p.px - base[i].px),
    round(p.py - base[i].py),
  ]);
  const leader = offset.map(([dx, dy]) => Math.hypot(dx, dy) > CREST.LEADER_MIN_SHIFT);
  let minSpacing = Infinity;
  for (let j = 0; j < n; j++) {
    for (let k = j + 1; k < n; k++) {
      const d = Math.hypot(pos[k].px - pos[j].px, pos[k].py - pos[j].py);
      if (d < minSpacing) minSpacing = d;
    }
  }
  return { base, offset, leader, minSpacing };
}

/**
 * 命中层直径 = clamp(实际最小间距, 徽章边长, HIT_MAX)。
 * 不拍一个固定 44px:360px 宽下固定 44px 的命中圈会互相重叠、点错邻居;
 * 用避让实际保证的间距做直径,是"不会偷邻居点击"的最大值,宽屏上自然到 44。
 */
export function hitSizeFor(layout: CrestLayout | null, crestSize: number): number {
  if (!layout || !Number.isFinite(layout.minSpacing)) return CREST.HIT_MAX;
  return Math.round(Math.max(crestSize, Math.min(CREST.HIT_MAX, layout.minSpacing)));
}

/** image:// 队徽符号;后端确无本地已验证 PNG(crest_url 为 null)时退回圆点,由调用方配色 + 队名标签兜底。 */
export function crestSymbol(crestUrl: string | null | undefined): string {
  return crestUrl ? `image://${crestUrl}` : "circle";
}

/**
 * 粗略估算文字宽度(CJK 按 fontSize 记一个字宽,ASCII 按 0.58×fontSize 记),
 * 用于判断"队名标签会不会盖住旁边的队徽"。不需要真实测量精度(canvas
 * measureText 在纯函数里也拿不到),只需要足够保守——宁可少显示几个标签,
 * 也不漏判导致标签真的压在别的队徽上。
 */
export function estimateLabelWidth(text: string, fontSize: number): number {
  let w = 0;
  for (const ch of text) {
    w += (ch.codePointAt(0) ?? 0) > 0x2e80 ? fontSize : fontSize * 0.58;
  }
  return w;
}

export type LabelCandidate = {
  /** 该点的绘制中心(真实坐标 + 避让偏移后的最终像素位置) */
  cx: number;
  cy: number;
  /** 该点自身的符号半径(选中态会放大) */
  radius: number;
  /** 想显示的文字;null 表示这个点本来就不打算显示名字——不参与判定,
   *  但仍然作为"别人可能撞上的队徽"参与别的候选的碰撞检测。 */
  text: string | null;
};

/**
 * 队名标签压住旁边队徽的 bug 修复(2026-09-09 用户反馈):ECharts 的
 * `labelLayout.hideOverlap` 只比较"标签 vs 标签",不知道画布上还有别的图片
 * 符号——一个标签能顺利避开所有其它标签,却仍然整个盖在旁边一支球队的队徽
 * 上面。图右上角"看起来没问题"只是因为附近恰好没有别的队徽,稀疏区侥幸
 * 不代表逻辑是对的,密集区(左下角)就会暴露。
 *
 * 在喂给 ECharts 之前,用真实绘制坐标 + 估算文字宽度做一次"标签矩形 vs 别的
 * 队徽圆"相交测试,提前把会压住队徽的标签摘掉——被摘掉的只是那个飘在图上的
 * 名字,队徽本身、点击选中、下方分组名单里的队名一个不少。
 * label 位置固定为 position:"top"(挂在符号正上方),与 quadrantOption.ts /
 * TeamStyleQuadrant.tsx 的实际渲染配置保持一致。
 */
export function resolveLabelVisibility(
  candidates: LabelCandidate[],
  opts: { fontSize: number; distance?: number; lineHeight?: number },
): boolean[] {
  const distance = opts.distance ?? 5;
  const height = opts.lineHeight ?? opts.fontSize * 1.4;
  return candidates.map((c, i) => {
    if (!c.text) return false;
    const width = estimateLabelWidth(c.text, opts.fontSize);
    const bottom = c.cy - c.radius - distance;
    const top = bottom - height;
    const left = c.cx - width / 2;
    const right = c.cx + width / 2;
    for (let j = 0; j < candidates.length; j++) {
      if (j === i) continue;
      const o = candidates[j];
      // 矩形上离对方圆心最近的点,量它到圆心的距离是否小于对方半径
      const nx = Math.max(left, Math.min(o.cx, right));
      const ny = Math.max(top, Math.min(o.cy, bottom));
      if (Math.hypot(o.cx - nx, o.cy - ny) < o.radius) return false;
    }
    return true;
  });
}
