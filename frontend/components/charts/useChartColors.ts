"use client";

/**
 * ECharts 图表配色:从设计系统 CSS 变量(frontend/app/globals.css)读取,
 * 深浅模式切换时自动重新解析——从 components/matches/TeamStyleQuadrant.tsx
 * 提取为共享 hook,供所有硬编码调色板的图表组件复用。
 *
 * ECharts canvas 不认 CSS 变量,必须读运行期解析值(同
 * components/matches/OddsTimeline.tsx 的既有做法)——而且不能直接读
 * --brand-navy:该 token 在深色模式下改写成 #061923,和深色模式的页面
 * 背景(#07161e)几乎同色,当强调色会糊到看不见。这里改用 --brand-blue,
 * 深浅两色都是明确可辨识的中亮度蓝,专门作为图表第二强调色使用。
 */

import { useSyncExternalStore } from "react";

export const THEME_CHANGE_EVENT = "allwin-theme-change";

export type ChartColors = {
  /** 主强调色(青绿,--brand-teal)——主队/主要系列/正向数据 */
  teal: string;
  /** 次强调色(中亮度蓝,读 --brand-blue 而非 --brand-navy)——客队/次要系列 */
  navy: string;
  win: string;
  loss: string;
  draw: string;
  /** 最深/最亮的正文墨色(--ink)——浅色模式深、深色模式亮,与背景色永远反向。
   * 2026-08-24 新增:射门落点图的标记描边需要一个"不管主题怎么切都能跟中性
   * 球场底色(浅 #F8FAFA / 深 #333333)拉开对比度"的颜色——硬编码白色描边在
   * 浅色球场上实测只有 1.05:1(近乎全白压全白),换成 --ink 后两个主题下都
   * ≥11:1(见 frontend/tests/shot-map-contrast.test.ts)。 */
  ink: string;
  ink2: string;
  ink3: string;
  /** 中性灰(--border-strong)——背景球队/非高亮点 */
  grey: string;
  surface: string;
  /** 2026-08-24 新增,供 matchTeamColors.ts 的对比度回退逻辑使用。 */
  /** 当前是否深色模式,与 ThemeToggle.tsx 写入 `document.documentElement.
   * dataset.theme` 的同一信号源——球队真实配色要按当前主题选浅/深色变体。 */
  isDark: boolean;
  /** 中性球场底色(--pitch-neutral-bg),射门落点图的真实渲染背景;球队配色
   * 要对着这个而不是页面卡片背景算对比度(见 FootballPitchBackground.tsx)。 */
  pitchBg: string;
};

/** SSR/首次渲染安全的字面量兜底(浅色值)——真实值在挂载后由 readChartColors() 覆盖。 */
const FALLBACK_COLORS: ChartColors = {
  teal: "#087e78",
  navy: "#1d6f8b",
  win: "#287851",
  loss: "#b83b2d",
  draw: "#706c64",
  ink: "#0d2c3d",
  ink2: "#40535d",
  ink3: "#5a6b73",
  grey: "#b8c6c6",
  surface: "#ffffff",
  isDark: false,
  pitchBg: "#f8fafa",
};

function readChartColors(): ChartColors {
  const style = getComputedStyle(document.documentElement);
  const readVar = (name: string, fallback: string) =>
    style.getPropertyValue(name).trim() || fallback;
  return {
    teal: readVar("--brand-teal", FALLBACK_COLORS.teal),
    navy: readVar("--brand-blue", FALLBACK_COLORS.navy),
    win: readVar("--win", FALLBACK_COLORS.win),
    loss: readVar("--loss", FALLBACK_COLORS.loss),
    draw: readVar("--draw", FALLBACK_COLORS.draw),
    ink: readVar("--ink", FALLBACK_COLORS.ink),
    ink2: readVar("--ink-2", FALLBACK_COLORS.ink2),
    ink3: readVar("--ink-3", FALLBACK_COLORS.ink3),
    grey: readVar("--border-strong", FALLBACK_COLORS.grey),
    surface: readVar("--surface", FALLBACK_COLORS.surface),
    isDark: document.documentElement.dataset.theme === "dark",
    pitchBg: readVar("--pitch-neutral-bg", FALLBACK_COLORS.pitchBg),
  };
}

// useSyncExternalStore 要求 getSnapshot 在值未变时返回同一引用(Object.is),
// 否则每次渲染都判定"变了"造成无限重渲染——模块级缓存 + 逐字段比较,
// 只有主题真的切换时才产生新对象。
let cachedColors: ChartColors | null = null;
function getChartColorsSnapshot(): ChartColors {
  const next = readChartColors();
  if (
    cachedColors &&
    (Object.keys(next) as (keyof ChartColors)[]).every(
      (key) => cachedColors![key] === next[key],
    )
  ) {
    return cachedColors;
  }
  cachedColors = next;
  return next;
}

function subscribeThemeChange(callback: () => void) {
  window.addEventListener(THEME_CHANGE_EVENT, callback);
  return () => window.removeEventListener(THEME_CHANGE_EVENT, callback);
}

/** 深浅色切换时重新解析 CSS 变量;服务端渲染与首帧水合前给字面量兜底。 */
export function useChartColors(): ChartColors {
  return useSyncExternalStore(
    subscribeThemeChange,
    getChartColorsSnapshot,
    () => FALLBACK_COLORS,
  );
}

/**
 * 十六进制颜色转 rgba() 字符串,带透明度。
 * ECharts visualMap/heatmap 渐变需要真实颜色值参与插值,不能用 CSS
 * color-mix() 之类的函数写法(canvas 渲染,不经过浏览器 CSS 引擎解析)。
 */
export function hexToRgba(hex: string, alpha: number): string {
  const raw = hex.replace("#", "");
  const full = raw.length === 3 ? raw.split("").map((c) => c + c).join("") : raw;
  const r = parseInt(full.slice(0, 2), 16) || 0;
  const g = parseInt(full.slice(2, 4), 16) || 0;
  const b = parseInt(full.slice(4, 6), 16) || 0;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
