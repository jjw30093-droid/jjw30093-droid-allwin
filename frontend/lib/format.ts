/**
 * 全站共享的数字展示格式化函数。
 *
 * 现状是百分比在 zh.ts、WinProbabilityBar、track-record、reco 等处各写了一份
 * `pct`,小数位 0 位与 1 位混用;赔率数字直接用 `toFixed(2)` 时各处调用点不统一
 * 也偶有遗漏,出现 "4.1" 这种末位未补零的写法;xG 小数位 1/2/3 位随手混用。
 * 这里统一成三个纯函数作为唯一真源,后续调用点逐步迁移过来,不在此文件里
 * 修改任何调用点。
 */

/** 0-1 的小数概率格式化为百分比字符串,如 0.618 → "62%"(digits=0,四舍五入)
 * 或 "61.8%"(digits=1)。 */
export function formatPct(p: number, digits: 0 | 1 = 0): string {
  return `${(p * 100).toFixed(digits)}%`;
}

/** 赔率固定两位小数并补零,如 4 → "4.00",1.9 → "1.90",1.85 → "1.85"。 */
export function formatOdds(v: number): string {
  return v.toFixed(2);
}

/** xG 数值格式化,默认两位小数,如 1.5 → "1.50"。 */
export function formatXg(v: number, digits: 1 | 2 = 2): string {
  return v.toFixed(digits);
}

/**
 * 一组互斥、合计为 1 的概率 → 整数百分比,合计**恒等于 100**(最大余数法,
 * 2026-09-26)。逐项四舍五入会出现合计 99/101(线上真实例子:0.3866/0.3067/
 * 0.3067 各自取整是 39/31/31,合计 101)。做法:先按比例换成百分数并各自向下
 * 取整,把差额(100 − 取整之和)逐个补给小数部分最大的那几项;小数部分相同则
 * 原值大的优先,再相同按下标靠前。
 *
 * - 输入先按合计归一化:去水后的概率四位小数取整,合计可能是 0.9999/1.0001。
 * - 任一项不是有限数、为负,或合计 ≤ 0 → 返回 null,调用方不画(不补 0、不猜)。
 * - 浮点误差:33.3+33.3+33.4 这类刚好落在整数边界的值,用 1e-9 容差比较。
 * 全站所有"三项概率同时展示"的地方必须走这个函数,不许各自 Math.round。
 */
export function roundToHundred(probs: number[]): number[] | null {
  if (!probs.length || probs.some((p) => !Number.isFinite(p) || p < 0)) return null;
  const total = probs.reduce((a, b) => a + b, 0);
  if (!(total > 0)) return null;
  const EPS = 1e-9;
  const exact = probs.map((p) => (p / total) * 100);
  const floors = exact.map((x) => Math.floor(x + EPS));
  let deficit = 100 - floors.reduce((a, b) => a + b, 0);
  const order = exact
    .map((x, i) => ({ i, frac: x - Math.floor(x + EPS), x }))
    .sort((a, b) => (Math.abs(b.frac - a.frac) > EPS ? b.frac - a.frac : b.x !== a.x ? b.x - a.x : a.i - b.i));
  const out = [...floors];
  for (const o of order) {
    if (deficit <= 0) break;
    out[o.i] += 1;
    deficit -= 1;
  }
  return out;
}

/** 胜平负三项 → [主胜, 平局, 客胜] 整数百分比,合计恒为 100;无效输入返回 null。 */
export function roundWdlPct(pHome: number, pDraw: number, pAway: number): [number, number, number] | null {
  const r = roundToHundred([pHome, pDraw, pAway]);
  return r ? [r[0], r[1], r[2]] : null;
}
