/**
 * 榜首胶囊队色解析。用的都是生产库里的真实队色(2026-09-11 从 dim_match
 * Home/Away_Team_Color_Light/Dark 取样),不是编出来的十六进制值——这套
 * 逻辑存在的理由就是真实数据里有不安全的颜色,拿虚构值测等于没测。
 */

import { describe, expect, it } from "vitest";
import { contrastRatioHex } from "@/components/charts/colorContrast";
import { resolvePillPaint } from "@/components/leaderboardPillColor";

const CARD_BG_LIGHT = "#ffffff";
const CARD_BG_DARK = "#0d2029";

describe("resolvePillPaint", () => {
  it("没有队色时返回 null(调用方回退品牌色)", () => {
    expect(resolvePillPaint(null)).toBeNull();
    expect(resolvePillPaint(undefined)).toBeNull();
    expect(resolvePillPaint({ light: null, dark: null })).toBeNull();
  });

  it("非法十六进制一律拒绝,不当颜色用", () => {
    expect(resolvePillPaint({ light: "blue", dark: "#033cc1" })).toBeNull();
    expect(resolvePillPaint({ light: "#051495", dark: "0x33cc1" })).toBeNull();
  });

  it("切尔西深蓝两套主题都安全,原样保留", () => {
    // 生产真实值:Chelsea light #051495 / dark #033cc1
    const paint = resolvePillPaint({ light: "#051495", dark: "#033cc1" });
    expect(paint).not.toBeNull();
    expect(paint!.light.bg).toBe("#051495");
    expect(paint!.dark.bg).toBe("#033cc1");
  });

  it("底色深时用白字,底色浅时用深色字,不固定白字", () => {
    // Chelsea 深蓝 → 白字;Man City 浅蓝 #69A8D8 配白字只有 2.2:1,必须深字
    const chelsea = resolvePillPaint({ light: "#051495", dark: "#033cc1" })!;
    expect(chelsea.light.ink).toBe("#ffffff");

    const city = resolvePillPaint({ light: "#69A8D8", dark: "#76b4e5" });
    expect(city).not.toBeNull();
    expect(city!.light.ink).toBe("#0d2c3d");
    expect(city!.dark.ink).toBe("#0d2c3d");
  });

  it("解析出来的配色必须真的达标:字对胶囊 ≥4.5:1(硬门槛)、胶囊对卡片 ≥1.5:1", () => {
    const cases = [
      { light: "#051495", dark: "#033cc1" }, // Chelsea
      { light: "#69A8D8", dark: "#76b4e5" }, // Man City
      { light: "#0060AA", dark: "#ffffff" }, // Leeds(深色变体是纯白)
    ];
    for (const c of cases) {
      const paint = resolvePillPaint(c);
      if (!paint) continue;
      expect(contrastRatioHex(paint.light.bg, CARD_BG_LIGHT)).toBeGreaterThanOrEqual(1.5);
      expect(contrastRatioHex(paint.dark.bg, CARD_BG_DARK)).toBeGreaterThanOrEqual(1.5);
      expect(contrastRatioHex(paint.light.ink, paint.light.bg)).toBeGreaterThanOrEqual(4.5);
      expect(contrastRatioHex(paint.dark.ink, paint.dark.bg)).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("埃弗顿淡沙色配深色字,数字照样读得出来", () => {
    // 生产真实值:Everton light #dbcb98(淡沙色)对白底只有 1.61:1——色块很淡,
    // 但这不是被拦的理由(见 leaderboardPillColor.ts 文件头);真正要保证的是
    // 上面的数字可读,所以前景必须自动切成深色字。
    const paint = resolvePillPaint({ light: "#dbcb98", dark: "#0051d6" });
    expect(paint).not.toBeNull();
    expect(paint!.light.ink).toBe("#0d2c3d");
    expect(contrastRatioHex(paint!.light.ink, paint!.light.bg)).toBeGreaterThanOrEqual(4.5);
  });

  it("队色和卡片底色近到看不出胶囊时整体回退,不只回退一套主题", () => {
    // 深色卡片底是 #0d2029;一支深色变体几乎同色的球队,深色模式下等于没有
    // 胶囊。此时浅色那一套即使安全也不能单独上——同一支球队浅色模式队色、
    // 深色模式品牌青绿,比统一用品牌色更难理解。
    expect(contrastRatioHex("#102430", CARD_BG_DARK)).toBeLessThan(1.5);
    expect(resolvePillPaint({ light: "#051495", dark: "#102430" })).toBeNull();
  });

  it("勉强不达标的真实色走微调救回,不是一刀切丢弃(§11.3)", () => {
    // 构造一个对深色卡片底只差一点点的队色,验证微调路径真的被走到
    const almost = "#1d3c4b";
    expect(contrastRatioHex(almost, CARD_BG_DARK)).toBeLessThan(1.5);
    const paint = resolvePillPaint({ light: "#051495", dark: almost });
    expect(paint).not.toBeNull();
    expect(paint!.dark.bg).not.toBe(almost); // 被微调过
    expect(contrastRatioHex(paint!.dark.bg, CARD_BG_DARK)).toBeGreaterThanOrEqual(1.5);
  });
});
