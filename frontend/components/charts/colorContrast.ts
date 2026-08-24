/**
 * WCAG 合成对比度数学(2026-08-24)。
 *
 * 从 frontend/tests/shot-map-contrast.test.ts 提出来的同一份实现——那份
 * 测试原本手写了这三个函数,是因为当时颜色都是编译期已知的静态十六进制值,
 * 没有运行期需要用到这套数学的生产代码。matchTeamColors.ts 引入球队真实
 * 配色(外部动态值,不可能靠静态 fixture 穷举)后,这套对比度检查第一次
 * 需要在生产代码里真正跑起来,所以提成共享模块——测试和生产代码用同一份
 * 实现,不允许两边各写一份互相漂移。
 */

export type Rgb = [number, number, number];

export function hexToRgb(hex: string): Rgb {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  return [
    parseInt(full.slice(0, 2), 16),
    parseInt(full.slice(2, 4), 16),
    parseInt(full.slice(4, 6), 16),
  ];
}

/** 十六进制颜色是否是合法的 #rgb / #rrggbb 形式——球队配色来自外部数据源,
 * 用之前必须校验,不能假定它总是干净的十六进制字符串。 */
export function isValidHexColor(value: unknown): value is string {
  return typeof value === "string" && /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(value);
}

export function relativeLuminance([r, g, b]: Rgb): number {
  const s = [r, g, b].map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * s[0] + 0.7152 * s[1] + 0.0722 * s[2];
}

export function contrastRatio(a: Rgb, b: Rgb): number {
  const [l1, l2] = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x);
  return (l1 + 0.05) / (l2 + 0.05);
}

/** 两个十六进制颜色直接算对比度,跳过手动 hexToRgb 的调用方样板代码。 */
export function contrastRatioHex(fgHex: string, bgHex: string): number {
  return contrastRatio(hexToRgb(fgHex), hexToRgb(bgHex));
}

/** WCAG 非文字图形最低对比度要求(CLAUDE.md §11.3)。 */
export const MIN_CONTRAST = 3;
