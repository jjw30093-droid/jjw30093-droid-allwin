/**
 * matchTeamColors.ts 单测(2026-08-24 建,2026-08-26 微调分支,2026-09-26 第四批统一取色)。
 *
 * resolveMatchColors 是全站唯一取色入口:本场 FotMob 配色 → 该队近期代表色 → 兜底组合,
 * 每一级都做"对比度 ≥3:1 + 主客 RGB 距离 ≥100"两项检查,任一方不过整对退级。
 * 外部数据是动态值,不可能用静态十六进制穷举,所以直接单测函数行为(CLAUDE.md §11.3:
 * 用合成对比度断言保护,不只测"没抛异常")。
 */

import { describe, expect, it } from "vitest";
import {
  DARK_NUDGE_LIGHTNESS,
  LIGHT_NUDGE_LIGHTNESS,
  MATCH_FALLBACK_COLORS,
  resolveMatchColors,
  resolveTeamColor,
} from "@/components/charts/matchTeamColors";
import {
  colorsDistinct,
  contrastRatioHex,
  hexToRgb,
  MIN_CONTRAST,
  rgbToHsl,
} from "@/components/charts/colorContrast";

const LIGHT_SURFACE = "#ffffff";
const DARK_SURFACE = "#0d2029"; // 深色 --surface
const LIGHT_PITCH = "#f8fafa";
const DARK_PITCH = "#333333";

const BACKGROUNDS = [
  { name: "浅色卡片底", isDark: false, bg: LIGHT_SURFACE },
  { name: "深色卡片底", isDark: true, bg: DARK_SURFACE },
  { name: "浅色球场底", isDark: false, bg: LIGHT_PITCH },
  { name: "深色球场底", isDark: true, bg: DARK_PITCH },
] as const;

function hue(hex: string): number {
  return rgbToHsl(hexToRgb(hex))[0];
}

describe("resolveTeamColor:单个颜色", () => {
  it("浅色模式选 light 变体,对比度达标时原样返回、adjusted=false", () => {
    expect(resolveTeamColor({ light: "#104070", dark: "#035db8" }, { isDark: false, backgroundHex: LIGHT_PITCH })).toEqual({
      hex: "#104070",
      adjusted: false,
    });
  });

  it("深色模式选 dark 变体(纯白对深色球场 12.6:1,原样返回)", () => {
    expect(resolveTeamColor({ light: "#d99f00", dark: "#ffffff" }, { isDark: true, backgroundHex: DARK_PITCH })).toEqual({
      hex: "#ffffff",
      adjusted: false,
    });
  });

  // ── 第四批:深色模式明度预算放宽到 25%,保持色相只提亮 ──
  for (const hex of ["#0057a3", "#ab2151", "#0039d0"]) {
    for (const bg of [DARK_SURFACE, DARK_PITCH]) {
      it(`深色下 ${hex}(FotMob darkMode 真实值,对 ${bg} 不足 3:1)提亮后保留:达标、色相不变、变亮`, () => {
        expect(contrastRatioHex(hex, bg)).toBeLessThan(MIN_CONTRAST); // 前提:原值确实不达标
        const r = resolveTeamColor({ dark: hex }, { isDark: true, backgroundHex: bg });
        expect(r).not.toBeNull();
        expect(r!.adjusted).toBe(true);
        expect(contrastRatioHex(r!.hex, bg)).toBeGreaterThanOrEqual(MIN_CONTRAST);
        // 只提亮:HSL 明度上升,色相基本不变(取整误差 ≤0.01)
        expect(rgbToHsl(hexToRgb(r!.hex))[2]).toBeGreaterThan(rgbToHsl(hexToRgb(hex))[2]);
        expect(Math.abs(hue(r!.hex) - hue(hex))).toBeLessThan(0.01);
        // 调整幅度在深色预算内
        expect(rgbToHsl(hexToRgb(r!.hex))[2] - rgbToHsl(hexToRgb(hex))[2]).toBeLessThanOrEqual(DARK_NUDGE_LIGHTNESS + 1e-9);
      });
    }
  }

  // ── 浅色模式预算 15%(第四批,由 6% 放宽):能救的救,救不了返回 null(由整对退级) ──
  it("浅色下 #e09d00 对白色卡片底(2.34:1):预算内变暗后达标,保留、色相不变", () => {
    const r = resolveTeamColor({ light: "#e09d00" }, { isDark: false, backgroundHex: LIGHT_SURFACE });
    expect(r).not.toBeNull();
    expect(r!.adjusted).toBe(true);
    expect(contrastRatioHex(r!.hex, LIGHT_SURFACE)).toBeGreaterThanOrEqual(MIN_CONTRAST);
    expect(Math.abs(hue(r!.hex) - hue("#e09d00"))).toBeLessThan(0.01);
    expect(rgbToHsl(hexToRgb(r!.hex))[2]).toBeLessThan(rgbToHsl(hexToRgb("#e09d00"))[2]);
  });

  it("浅色下 #e09d00 对浅色球场底 #f8fafa:15% 预算内变暗后达标(6% 时救不回来),色相不变", () => {
    const r = resolveTeamColor({ light: "#e09d00" }, { isDark: false, backgroundHex: LIGHT_PITCH });
    expect(r).not.toBeNull();
    expect(r!.adjusted).toBe(true);
    expect(contrastRatioHex(r!.hex, LIGHT_PITCH)).toBeGreaterThanOrEqual(MIN_CONTRAST);
    expect(Math.abs(hue(r!.hex) - hue("#e09d00"))).toBeLessThan(0.01);
  });

  it("浅色下 #67ade0(2.43:1):15% 预算内变暗后保留(卡片底与球场底),色相不变、变暗、幅度在预算内", () => {
    for (const bg of [LIGHT_SURFACE, LIGHT_PITCH]) {
      const r = resolveTeamColor({ light: "#67ade0" }, { isDark: false, backgroundHex: bg });
      expect(r, bg).not.toBeNull();
      expect(r!.adjusted).toBe(true);
      expect(contrastRatioHex(r!.hex, bg)).toBeGreaterThanOrEqual(MIN_CONTRAST);
      expect(Math.abs(hue(r!.hex) - hue("#67ade0"))).toBeLessThan(0.01);
      const dl = rgbToHsl(hexToRgb("#67ade0"))[2] - rgbToHsl(hexToRgb(r!.hex))[2];
      expect(dl).toBeGreaterThan(0);
      expect(dl).toBeLessThanOrEqual(LIGHT_NUDGE_LIGHTNESS + 1e-9);
    }
  });

  it("浅色下需要超过 15% 预算才能达标的颜色(亮黄 #ffee88 对白底约 1.2:1)→ null,整对退级,不硬拗", () => {
    expect(resolveTeamColor({ light: "#ffee88" }, { isDark: false, backgroundHex: LIGHT_SURFACE })).toBeNull();
    expect(resolveTeamColor({ light: "#ffee88" }, { isDark: false, backgroundHex: LIGHT_PITCH })).toBeNull();
  });

  it("预算常量:浅色 15%、深色 25%", () => {
    expect(LIGHT_NUDGE_LIGHTNESS).toBe(0.15);
    expect(DARK_NUDGE_LIGHTNESS).toBe(0.25);
  });

  it("2026-08-26 事故复现:瓦伦西亚 #ff671f 对白底 2.91:1,微调后保留真实色", () => {
    const r = resolveTeamColor({ light: "#ff671f" }, { isDark: false, backgroundHex: LIGHT_SURFACE });
    expect(r).not.toBeNull();
    expect(r!.adjusted).toBe(true);
    expect(contrastRatioHex(r!.hex, LIGHT_SURFACE)).toBeGreaterThanOrEqual(MIN_CONTRAST);
  });

  it("白色描边对浅色球场(白压白)→ null", () => {
    expect(resolveTeamColor({ light: "#ffffff" }, { isDark: false, backgroundHex: LIGHT_PITCH })).toBeNull();
  });

  it("缺失/非法输入返回 null,不抛异常;同主题变体缺失不跨主题借用", () => {
    const o = { isDark: false, backgroundHex: LIGHT_PITCH } as const;
    expect(resolveTeamColor(null, o)).toBeNull();
    expect(resolveTeamColor(undefined, o)).toBeNull();
    expect(resolveTeamColor({ light: "rgba(255,0,0,0.5)" }, o)).toBeNull();
    expect(resolveTeamColor({ light: "" }, o)).toBeNull();
    expect(resolveTeamColor({ light: null, dark: "#ffffff" }, o)).toBeNull(); // 不把 dark 当 light 用
  });
});

describe("resolveMatchColors:取色顺序与整对退级", () => {
  const opts = { isDark: false, backgroundHex: LIGHT_SURFACE } as const;
  const MATCH_HOME = { light: "#f13c26", dark: "#f13c26" };
  const MATCH_AWAY = { light: "#104070", dark: "#035db8" };
  const TEAM_HOME = { light: "#0a7d3b", dark: "#3fbf6f" };
  const TEAM_AWAY = { light: "#6a1b9a", dark: "#b26be0" };

  it("① 本场配色可用 → level=match,主客各取各的", () => {
    const r = resolveMatchColors(
      { home: { match: MATCH_HOME, team: TEAM_HOME }, away: { match: MATCH_AWAY, team: TEAM_AWAY } },
      opts,
    );
    expect(r).toMatchObject({ home: "#f13c26", away: "#104070", level: "match", homeAdjusted: false, awayAdjusted: false });
  });

  it("② 本场缺失 → 退到该队近期代表色(level=team)", () => {
    const r = resolveMatchColors({ home: { team: TEAM_HOME }, away: { team: TEAM_AWAY } }, opts);
    expect(r).toMatchObject({ home: "#0a7d3b", away: "#6a1b9a", level: "team" });
  });

  it("③ 本场、近期都缺失 → 兜底组合(浅色/深色各一份)", () => {
    const light = resolveMatchColors({}, opts);
    expect(light).toMatchObject({ ...MATCH_FALLBACK_COLORS.light, level: "fallback" });
    const dark = resolveMatchColors({}, { isDark: true, backgroundHex: DARK_SURFACE });
    expect(dark).toMatchObject({ ...MATCH_FALLBACK_COLORS.dark, level: "fallback" });
  });

  it("任一方不通过 → 整对退级:主队本场色可用、客队本场色缺失,不混搭(不出现主队真实色 + 客队兜底色)", () => {
    const r = resolveMatchColors(
      { home: { match: MATCH_HOME, team: TEAM_HOME }, away: { match: null, team: TEAM_AWAY } },
      opts,
    );
    expect(r.level).toBe("team");
    expect(r.home).toBe("#0a7d3b");
    expect(r.away).toBe("#6a1b9a");
  });

  it("主客近色(RGB 距离 <100)→ 整对退级", () => {
    // 两个几乎一样的蓝:各自对比度达标,但彼此难分
    const close = { light: "#104070" };
    const close2 = { light: "#1a4a7a" };
    expect(colorsDistinct("#104070", "#1a4a7a")).toBe(false);
    const r = resolveMatchColors(
      { home: { match: close, team: TEAM_HOME }, away: { match: close2, team: TEAM_AWAY } },
      opts,
    );
    expect(r.level).toBe("team");
  });

  it("本场、近期都近色 → 落到兜底,兜底组合两色可区分", () => {
    const close = { light: "#104070" };
    const close2 = { light: "#1a4a7a" };
    const r = resolveMatchColors({ home: { match: close, team: close }, away: { match: close2, team: close2 } }, opts);
    expect(r.level).toBe("fallback");
    expect(colorsDistinct(r.home, r.away)).toBe(true);
  });

  it("对比度不过:一方本场色过浅(白压白)→ 整对退到下一级", () => {
    const r = resolveMatchColors(
      { home: { match: { light: "#ffffff" }, team: TEAM_HOME }, away: { match: MATCH_AWAY, team: TEAM_AWAY } },
      opts,
    );
    expect(r.level).toBe("team");
  });

  it("深色模式:本场 darkMode 深蓝被提亮后保留(level=match,adjusted=true),而不是退兜底", () => {
    const r = resolveMatchColors(
      { home: { match: { dark: "#f13c26" } }, away: { match: { dark: "#0057a3" } } },
      { isDark: true, backgroundHex: DARK_SURFACE },
    );
    expect(r.level).toBe("match");
    expect(r.away).toBe("#0069c4");
    expect(r.awayAdjusted).toBe(true);
    expect(r.homeAdjusted).toBe(false);
  });

  it("提亮后主客变得难分 → 检查用的是调整后的颜色,整对退级", () => {
    // 两个深蓝(#0057a3 / #0039d0)提亮后都成亮蓝,RGB 距离 < 100
    const r = resolveMatchColors(
      { home: { match: { dark: "#0057a3" } }, away: { match: { dark: "#0039d0" } } },
      { isDark: true, backgroundHex: DARK_SURFACE },
    );
    expect(r.level).not.toBe("match");
  });
});

describe("兜底组合:两种主题 × 卡片底/球场底 都满足对比度与可区分(合成断言)", () => {
  for (const { name, isDark, bg } of BACKGROUNDS) {
    it(name, () => {
      const pair = isDark ? MATCH_FALLBACK_COLORS.dark : MATCH_FALLBACK_COLORS.light;
      expect(contrastRatioHex(pair.home, bg)).toBeGreaterThanOrEqual(MIN_CONTRAST);
      expect(contrastRatioHex(pair.away, bg)).toBeGreaterThanOrEqual(MIN_CONTRAST);
      expect(colorsDistinct(pair.home, pair.away)).toBe(true);
    });
  }
});

describe("不变量:无论输入是什么,输出的每个颜色对着背景 ≥3:1 且主客可区分", () => {
  const inputs = [
    "#0057a3", "#ab2151", "#0039d0", "#e09d00", "#67ade0", "#ffffff", "#000000", "#333333",
    "#f13c26", "#104070", "#035db8", "#ff671f", "#12a0d7", "#8b3249", "#c80010", "#007038",
  ];
  for (const { name, isDark, bg } of BACKGROUNDS) {
    it(`${name}:16 个真实/极端颜色两两组合(${inputs.length * inputs.length} 对)`, () => {
      for (const a of inputs) {
        for (const b of inputs) {
          const p = (hex: string) => (isDark ? { dark: hex } : { light: hex });
          const r = resolveMatchColors(
            { home: { match: p(a), team: p(b) }, away: { match: p(b), team: p(a) } },
            { isDark, backgroundHex: bg },
          );
          expect(contrastRatioHex(r.home, bg), `${a}/${b}`).toBeGreaterThanOrEqual(MIN_CONTRAST);
          expect(contrastRatioHex(r.away, bg), `${a}/${b}`).toBeGreaterThanOrEqual(MIN_CONTRAST);
          expect(colorsDistinct(r.home, r.away), `${a}/${b}`).toBe(true);
        }
      }
    });
  }
});
