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
