/**
 * matchTeamColors.ts 单测(2026-08-24,2026-08-26 补充微调分支)。
 *
 * resolveTeamColor/resolveMatchColors 是真实球队配色接入比赛详情图表的唯一
 * 安全阀——外部数据(FotMob teamColors)是动态值,不可能用静态十六进制值
 * 穷举覆盖(不同于 shot-map-contrast.test.ts 那份固定品牌色的 fixture),
 * 所以必须直接单测这个函数本身的行为:主题变体选对、对比度勉强不达标时
 * 小幅微调救回真实色、差得远时仍正确回退品牌色、缺失/非法输入不崩溃、
 * 不跨主题借用另一份颜色。
 *
 * 2026-08-26 真实事故:瓦伦西亚 vs 皇家贝蒂斯,主队真实色 #ff671f 对势头图
 * 白色卡片背景只有 2.91:1(比阈值 3:1 差 0.09),此前直接判定"不安全"丢弃
 * 换成品牌青绿,导致线上颜色和 FotMob 官方不一致。修复后勉强不达标的真实
 * 色应该被微调(不是丢弃),差得远的(如下面 #035db8 案例,需要 11% 明度
 * 才能达标,远超 6% 的微调预算)仍然正确回退。
 */

import { describe, expect, it } from "vitest";
import { resolveMatchColors, resolveTeamColor } from "@/components/charts/matchTeamColors";
import { contrastRatioHex } from "@/components/charts/colorContrast";

// 真实数据(tests/fixtures/fotmob/prematch-5104961.json,本会话已实测确认)。
const REAL_PAIR = { light: "#f13c26", dark: "#f13c26" };
const REAL_AWAY_PAIR = { light: "#104070", dark: "#035db8" };

const LIGHT_PITCH = "#f8fafa";
const DARK_PITCH = "#333333";
const FALLBACK = "#087e78";

describe("resolveTeamColor", () => {
  it("浅色模式选 light 变体,对比度达标时原样返回", () => {
    const v = resolveTeamColor(REAL_AWAY_PAIR, {
      isDark: false,
      backgroundHex: LIGHT_PITCH,
      fallbackHex: FALLBACK,
    });
    expect(v).toBe("#104070");
  });

  it("深色模式选 dark 变体,对比度达标时原样返回", () => {
    // 复用生产 API 实测过的真实值(西班牙人 vs 皇马,matchId 5868025):
    // 皇马深色模式配色是 #ffffff,对深色中性球场 #333333 有 12.6:1,达标。
    const v = resolveTeamColor(
      { light: "#d99f00", dark: "#ffffff" },
      { isDark: true, backgroundHex: DARK_PITCH, fallbackHex: FALLBACK },
    );
    expect(v).toBe("#ffffff");
  });

  it("深色模式变体本身对比度差得远(真实案例:#035db8 对深色球场 #333333 只有 1.96:1,需要约 11% 明度才能达标,远超 6% 微调预算)时回退品牌色", () => {
    const v = resolveTeamColor(REAL_AWAY_PAIR, {
      isDark: true,
      backgroundHex: DARK_PITCH,
      fallbackHex: FALLBACK,
    });
    expect(v).toBe(FALLBACK);
  });

  it("2026-08-26 真实事故复现:瓦伦西亚真实橙色 #ff671f 对白色卡片背景只差 0.09 就达标(2.91:1 vs 阈值 3:1),微调后应该是救回真实色而不是品牌色", () => {
    const v = resolveTeamColor(
      { light: "#ff671f", dark: "#ff9238" },
      { isDark: false, backgroundHex: "#ffffff", fallbackHex: FALLBACK },
    );
    expect(v).not.toBe(FALLBACK);
    expect(v.toLowerCase()).not.toBe("#ffffff");
    // 微调后必须真的达标,不能返回一个凑数但仍不安全的值
    expect(contrastRatioHex(v, "#ffffff")).toBeGreaterThanOrEqual(3);
  });

  it("已知不安全对比度(白色描边 vs 浅色球场,复用 shot-map-contrast.test.ts 实测过的真实案例)回退品牌色", () => {
    const v = resolveTeamColor(
      { light: "#ffffff", dark: null },
      { isDark: false, backgroundHex: LIGHT_PITCH, fallbackHex: FALLBACK },
    );
    expect(v).toBe(FALLBACK);
  });

  it("数据整体缺失(null/undefined)回退品牌色,不抛异常", () => {
    expect(
      resolveTeamColor(null, { isDark: false, backgroundHex: LIGHT_PITCH, fallbackHex: FALLBACK }),
    ).toBe(FALLBACK);
    expect(
      resolveTeamColor(undefined, {
        isDark: false,
        backgroundHex: LIGHT_PITCH,
        fallbackHex: FALLBACK,
      }),
    ).toBe(FALLBACK);
  });

  it("非法十六进制值(rgba()/空字符串)回退品牌色,不抛异常", () => {
    expect(
      resolveTeamColor(
        { light: "rgba(255,0,0,0.5)", dark: null },
        { isDark: false, backgroundHex: LIGHT_PITCH, fallbackHex: FALLBACK },
      ),
    ).toBe(FALLBACK);
    expect(
      resolveTeamColor(
        { light: "", dark: null },
        { isDark: false, backgroundHex: LIGHT_PITCH, fallbackHex: FALLBACK },
      ),
    ).toBe(FALLBACK);
  });

  it("同主题变体缺失时回退品牌色,不跨主题借用另一份颜色(深色模式客队可能给纯白,不能被当成浅色变体用在浅色球场上)", () => {
    const v = resolveTeamColor(
      { light: null, dark: "#ffffff" },
      { isDark: false, backgroundHex: LIGHT_PITCH, fallbackHex: FALLBACK },
    );
    expect(v).toBe(FALLBACK);
    expect(v).not.toBe("#ffffff");
  });
});

describe("resolveMatchColors", () => {
  it("主客队分别按各自数据解析,互不影响", () => {
    const { home, away } = resolveMatchColors(REAL_PAIR, REAL_AWAY_PAIR, {
      isDark: false,
      backgroundHex: LIGHT_PITCH,
      fallback: { home: "#087e78", away: "#1d6f8b" },
    });
    expect(home).toBe("#f13c26");
    expect(away).toBe("#104070");
  });

  it("双方数据都缺失时两个都回退各自的品牌色", () => {
    const { home, away } = resolveMatchColors(null, null, {
      isDark: false,
      backgroundHex: LIGHT_PITCH,
      fallback: { home: "#087e78", away: "#1d6f8b" },
    });
    expect(home).toBe("#087e78");
    expect(away).toBe("#1d6f8b");
  });
});
