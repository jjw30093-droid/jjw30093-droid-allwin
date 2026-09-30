// 回测用的统计小工具(纯函数)。

import { marginDist, poissonPmf } from "../market";

/** 独立 Poisson 的胜 / 平 / 负概率。 */
export function poisson1x2(lh: number, la: number, maxGoals = 12): [number, number, number] {
  const md = marginDist(poissonPmf(lh, maxGoals), poissonPmf(la, maxGoals));
  let h = 0;
  let d = 0;
  let a = 0;
  for (const [m, p] of md) {
    if (m > 0) h += p;
    else if (m === 0) d += p;
    else a += p;
  }
  const s = h + d + a;
  return [h / s, d / s, a / s];
}

export function outcomeIndex(score: [number, number]): 0 | 1 | 2 {
  return score[0] > score[1] ? 0 : score[0] === score[1] ? 1 : 2;
}

/** 三分类 Brier:Σ(p − y)²。 */
export function brier3(p: [number, number, number], outcome: 0 | 1 | 2): number {
  return p.reduce((s, v, i) => s + (v - (i === outcome ? 1 : 0)) ** 2, 0);
}

export function poissonLogLik(y: number, lam: number): number {
  let lf = 0;
  for (let i = 2; i <= y; i++) lf += Math.log(i);
  return y * Math.log(lam) - lam - lf;
}

/** 黄金分割求一维最大值。 */
export function goldenMax(f: (x: number) => number, lo: number, hi: number, tol = 1e-4): number {
  const g = (Math.sqrt(5) - 1) / 2;
  let a = lo;
  let b = hi;
  let c = b - g * (b - a);
  let d = a + g * (b - a);
  let fc = f(c);
  let fd = f(d);
  while (b - a > tol) {
    if (fc > fd) {
      b = d;
      d = c;
      fd = fc;
      c = b - g * (b - a);
      fc = f(c);
    } else {
      a = c;
      c = d;
      fc = fd;
      d = a + g * (b - a);
      fd = f(d);
    }
  }
  return (a + b) / 2;
}

/** 校准斜率:按预测概率分 bins 个等频箱,以箱内样本数加权最小二乘拟合"实际频率 ~ 平均预测"。 */
export function calibrationSlope(pairs: { p: number; y: number }[], bins = 10): { slope: number; intercept: number; table: { n: number; pred: number; obs: number }[] } {
  const sorted = [...pairs].sort((u, v) => u.p - v.p);
  const table: { n: number; pred: number; obs: number }[] = [];
  for (let b = 0; b < bins; b++) {
    const lo = Math.floor((b * sorted.length) / bins);
    const hi = Math.floor(((b + 1) * sorted.length) / bins);
    const chunk = sorted.slice(lo, hi);
    if (!chunk.length) continue;
    table.push({
      n: chunk.length,
      pred: chunk.reduce((s, x) => s + x.p, 0) / chunk.length,
      obs: chunk.reduce((s, x) => s + x.y, 0) / chunk.length,
    });
  }
  const W = table.reduce((s, r) => s + r.n, 0);
  const mx = table.reduce((s, r) => s + r.n * r.pred, 0) / W;
  const my = table.reduce((s, r) => s + r.n * r.obs, 0) / W;
  const sxy = table.reduce((s, r) => s + r.n * (r.pred - mx) * (r.obs - my), 0);
  const sxx = table.reduce((s, r) => s + r.n * (r.pred - mx) ** 2, 0);
  const slope = sxy / sxx;
  return { slope, intercept: my - slope * mx, table };
}

/** 6 段计时(1–15、16–30、31–45+、46–60、61–75、76–90+)。 */
export function timingBucket(minute: number): number {
  if (minute <= 45) return Math.min(Math.floor((Math.max(minute, 1) - 1) / 15), 2);
  return 3 + Math.min(Math.floor((minute - 46) / 15), 2);
}

export function mean(xs: number[]): number {
  return xs.length ? xs.reduce((s, x) => s + x, 0) / xs.length : Number.NaN;
}

/** 二项分布概率质量 P(X = k),X ~ Bin(n, p)。 */
export function binomPmf(k: number, n: number, p: number): number {
  if (p <= 0) return k === 0 ? 1 : 0;
  if (p >= 1) return k === n ? 1 : 0;
  let logC = 0;
  for (let i = 1; i <= k; i++) logC += Math.log(n - k + i) - Math.log(i);
  return Math.exp(logC + k * Math.log(p) + (n - k) * Math.log(1 - p));
}

/** 精确二项检验的双侧 p 值:把概率不大于 P(X = k) 的所有取值的概率相加(与 R binom.test 同口径,相对容差 1e-7)。 */
export function binomTwoSidedP(k: number, n: number, p: number): number {
  const pk = binomPmf(k, n, p);
  let sum = 0;
  for (let i = 0; i <= n; i++) {
    const pi = binomPmf(i, n, p);
    if (pi <= pk * (1 + 1e-7)) sum += pi;
  }
  return Math.min(1, sum);
}
