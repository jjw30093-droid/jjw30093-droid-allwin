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

export type Hsl = [number, number, number]; // [0..1, 0..1, 0..1]

export function rgbToHsl([r, g, b]: Rgb): Hsl {
  const rn = r / 255, gn = g / 255, bn = b / 255;
  const max = Math.max(rn, gn, bn), min = Math.min(rn, gn, bn);
  let h = 0;
  const l = (max + min) / 2;
  const d = max - min;
  const s = d === 0 ? 0 : l > 0.5 ? d / (2 - max - min) : d / (max + min);
  if (d !== 0) {
    switch (max) {
      case rn: h = (gn - bn) / d + (gn < bn ? 6 : 0); break;
      case gn: h = (bn - rn) / d + 2; break;
      default: h = (rn - gn) / d + 4;
    }
    h /= 6;
  }
  return [h, s, l];
}

export function hslToRgb([h, s, l]: Hsl): Rgb {
  if (s === 0) {
    const v = Math.round(l * 255);
    return [v, v, v];
  }
  const hue2rgb = (p: number, q: number, t: number): number => {
    let tt = t;
    if (tt < 0) tt += 1;
    if (tt > 1) tt -= 1;
    if (tt < 1 / 6) return p + (q - p) * 6 * tt;
    if (tt < 1 / 2) return q;
    if (tt < 2 / 3) return p + (q - p) * (2 / 3 - tt) * 6;
    return p;
  };
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  return [
    Math.round(hue2rgb(p, q, h + 1 / 3) * 255),
    Math.round(hue2rgb(p, q, h) * 255),
    Math.round(hue2rgb(p, q, h - 1 / 3) * 255),
  ];
}

export function rgbToHex([r, g, b]: Rgb): string {
  return `#${[r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

/** 允许为"接近达标"的真实颜色做的最大明度微调幅度(HSL lightness,0..1 量纲)。
 * 只用于救回勉强不达标的真实数据(如瓦伦西亚 #ff671f 对白底只差 2% 明度就
 * 能从 2.91:1 过到 3:1),不是把任意颜色硬拗到达标——真正对比度差得远的颜色
 * (如 #035db8 对深色球场需要 11% 才能达标)超出这个预算就该老实回退品牌色,
 * 而不是调整到面目全非还自称"这是球队真实色"。数值来源于两个真实案例的
 * 实测边界(见 frontend/tests/match-team-colors.test.ts),留出安全余量。 */
export const MAX_NUDGE_LIGHTNESS = 0.06;

/** 在 `MAX_NUDGE_LIGHTNESS` 预算内,把 fgHex 的明度朝着"远离背景明度"的方向
 * 微调,直到对比度达标或预算耗尽。预算内够不到阈值时返回 null——调用方
 * 应回退到品牌色,而不是使用一个调过头、不再代表真实球队色的十六进制值。
 *
 * 调整方向:比较前景/背景的相对亮度(WCAG relativeLuminance,不是 HSL
 * lightness 本身)决定该变暗还是变亮——前景比背景暗就继续变暗,前景比背景
 * 亮就继续变亮,这样每一步都在增大对比度,不会调反方向。 */
export function nudgeForContrast(
  fgHex: string,
  bgHex: string,
  minContrast: number = MIN_CONTRAST,
  maxLightnessShift: number = MAX_NUDGE_LIGHTNESS,
): string | null {
  const fgRgb = hexToRgb(fgHex);
  const bgRgb = hexToRgb(bgHex);
  if (contrastRatio(fgRgb, bgRgb) >= minContrast) return fgHex;

  // fg 比 bg 暗 → 继续变暗能拉大对比度(分母变小);fg 比 bg 亮 → 继续变亮
  // 才能拉大对比度(分子变大)。写反会把颜色调去让对比度变得更差,而不是更好。
  const darken = relativeLuminance(fgRgb) < relativeLuminance(bgRgb);
  const [h, s, l0] = rgbToHsl(fgRgb);
  const stepSize = 0.005;
  const steps = Math.round(maxLightnessShift / stepSize);
  for (let i = 1; i <= steps; i++) {
    const l = darken ? Math.max(0, l0 - i * stepSize) : Math.min(1, l0 + i * stepSize);
    const candidateHex = rgbToHex(hslToRgb([h, s, l]));
    if (contrastRatioHex(candidateHex, bgHex) >= minContrast) return candidateHex;
    if (l <= 0 || l >= 1) break;
  }
  return null;
}
