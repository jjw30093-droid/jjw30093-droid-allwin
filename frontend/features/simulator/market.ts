// p_eff 与公平线。p_eff 是 research/ah_signals/features_market.py::p_eff 的逐行移植
// (浏览器里跑模拟分布必须有 TS 版),与 Python 的一致性由
// tests/simulator-engine.test.ts 的 golden 值断言。λ 反推(fit_poisson)不移植:
// 只在导出脚本里用 Python 原实现算好,随 JSON 下发。

const TICK = 0.25;

export type IntDist = Map<number, number>;

function single(v: number, line: number): number {
  const adj = v - line;
  return Math.abs(adj) < 1e-9 ? 0 : adj > 0 ? 1 : -1;
}

/** EV 中性覆盖概率:(Wf + Wh/2)/(Wf + Wh/2 + Lf + Lh/2);"覆盖" = 结果 − line > 0。 */
export function pEff(dist: IntDist, line: number): number {
  const q = Math.round(line / TICK);
  const lines = q % 2 === 0 ? [line] : [line - TICK, line + TICK];
  let wf = 0;
  let wh = 0;
  let lf = 0;
  let lh = 0;
  for (const [v, p] of dist) {
    if (lines.length === 1) {
      const r = single(v, lines[0]);
      if (r > 0) wf += p;
      else if (r < 0) lf += p;
    } else {
      const s = single(v, lines[0]) + single(v, lines[1]);
      if (s === 2) wf += p;
      else if (s === 1) wh += p;
      else if (s === -1) lh += p;
      else if (s === -2) lf += p;
    }
  }
  const den = wf + wh / 2 + lf + lh / 2;
  return den > 0 ? (wf + wh / 2) / den : Number.NaN;
}

export function poissonPmf(lam: number, maxGoals = 10): number[] {
  const out = [Math.exp(-lam)];
  for (let k = 1; k <= maxGoals; k++) out.push((out[k - 1] * lam) / k);
  return out;
}

export function marginDist(ph: number[], pa: number[]): IntDist {
  const d: IntDist = new Map();
  ph.forEach((x, i) => pa.forEach((y, j) => d.set(i - j, (d.get(i - j) ?? 0) + x * y)));
  return d;
}

export function totalDist(ph: number[], pa: number[]): IntDist {
  const d: IntDist = new Map();
  ph.forEach((x, i) => pa.forEach((y, j) => d.set(i + j, (d.get(i + j) ?? 0) + x * y)));
  return d;
}

/** 使 p_eff 最接近 50% 的 0.25 档(规格 §8)。 */
export function fairLine(dist: IntDist, lo: number, hi: number): number {
  let best = lo;
  let bestGap = Infinity;
  for (let q = Math.round(lo / TICK); q <= Math.round(hi / TICK); q++) {
    const line = q * TICK;
    const gap = Math.abs(pEff(dist, line) - 0.5);
    if (gap < bestGap - 1e-12) {
      bestGap = gap;
      best = line;
    }
  }
  return best;
}
