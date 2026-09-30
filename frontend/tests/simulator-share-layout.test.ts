import { describe, expect, it } from "vitest";
import { fitRowNames, layoutFor, lineupLayout, NAME_GAP, NAME_MIN_PX, separateEndLabels, type Measure } from "@/features/simulator/shareImage";
import type { SnapLineupEntry } from "@/features/simulator/snapshot";

// 近似测量:中文字宽 = 字号,ASCII 0.55 字号,省略号 1 字号(与 Noto Sans SC 的方块字宽一致)
const measure: Measure = (t, px) => [...t].reduce((w, ch) => w + (/[\u0000-\u007f]/.test(ch) ? 0.55 * px : px), 0);

// 阿森纳最近一场首发(4-2-3-1,模板坐标,FotMob x 小 = 该队右路);画面上后卫线从左到右:卡拉菲奥里、加布里埃尔、孔萨、廷伯
const ARSENAL: SnapLineupEntry[] = [
  [11, "GK", "拉亚", "1", 0.5, 0.1],
  [32, "FB", "廷伯", "12", 0.12, 0.29],
  [34, "CB", "孔萨", "15", 0.38, 0.29],
  [36, "CB", "加布里埃尔", "6", 0.62, 0.29],
  [38, "FB", "卡拉菲奥里", "33", 0.88, 0.29],
  [64, "DM", "赖斯", "41", 0.3, 0.48],
  [66, "DM", "布鲁诺", "39", 0.7, 0.48],
  [83, "W", "萨卡", "7", 0.16, 0.68],
  [85, "AM", "厄德高", "8", 0.5, 0.68],
  [87, "W", "佐利斯", "17", 0.84, 0.68],
  [115, "ST", "哈弗茨", "29", 0.5, 0.87],
].map(([positionId, group, name, num, x, y]) => ({ positionId, group, playerId: String(positionId), name, num, x, y }) as SnapLineupEntry);

/** 分享图里主队阵容图的实际几何(与 drawLineups / drawPitch 同一套常数)。 */
function homeBox(size: 1350 | 1920) {
  const L = layoutFor(size);
  const w = (1080 - 2 * L.pad - 60) / 2;
  return { L, box: { x: L.pad + 20, py: 0, w, ph: L.lineups - 24 - 44 * L.s - 34 * L.s } };
}

function assertRowOk(players: ReturnType<typeof lineupLayout>, left: number, right: number) {
  const rows = new Map<number, typeof players>();
  for (const p of players) rows.set(p.cy, [...(rows.get(p.cy) ?? []), p]);
  for (const row of rows.values()) {
    const sorted = [...row].sort((a, b) => a.cx - b.cx);
    sorted.forEach((p, k) => {
      const w = measure(p.text, p.px);
      expect(p.px).toBeGreaterThanOrEqual(NAME_MIN_PX);
      expect(p.cx - w / 2).toBeGreaterThanOrEqual(left - 1e-9);
      expect(p.cx + w / 2).toBeLessThanOrEqual(right + 1e-9);
      if (k > 0) {
        const q = sorted[k - 1];
        expect(p.cx - w / 2 - (q.cx + measure(q.text, q.px) / 2)).toBeGreaterThanOrEqual(NAME_GAP - 1e-9);
      }
    });
  }
}

describe("分享图阵容:同一排名字不重叠(回归:阿森纳后卫线)", () => {
  for (const size of [1350, 1920] as const) {
    it(`${size}:后卫线四个名字完整显示且互不重叠`, () => {
      const { L, box } = homeBox(size);
      const out = lineupLayout(ARSENAL, box, L.s, measure);
      const back = out.filter((_, i) => ARSENAL[i].y === 0.29).map((p) => p.text);
      expect(back).toEqual(["廷伯", "孔萨", "加布里埃尔", "卡拉菲奥里"]);
      assertRowOk(out, box.x + 4, box.x + box.w - 4);
    });
  }

  it("放不下时先缩小该排字号,只影响这一排", () => {
    const box = { x: 0, py: 0, w: 400, ph: 300 };
    const out = lineupLayout(ARSENAL, box, 1, measure);
    const back = out.filter((_, i) => ARSENAL[i].y === 0.29);
    const gk = out[0];
    expect(back[0].px).toBeLessThan(17);
    expect(back[0].px).toBeGreaterThanOrEqual(NAME_MIN_PX);
    expect(back.map((p) => p.text)).toEqual(["廷伯", "孔萨", "加布里埃尔", "卡拉菲奥里"]);
    expect(gk.px).toBe(17);
    assertRowOk(out, box.x + 4, box.x + box.w - 4);
  });

  it("缩到下限仍放不下:截断并加省略号,仍不重叠", () => {
    const box = { x: 0, py: 0, w: 300, ph: 300 };
    const out = lineupLayout(ARSENAL, box, 1, measure);
    const back = out.filter((_, i) => ARSENAL[i].y === 0.29);
    expect(back[2].px).toBe(NAME_MIN_PX);
    expect(back[2].text.endsWith("…")).toBe(true);
    expect(back[3].text.endsWith("…")).toBe(true);
    assertRowOk(out, box.x + 4, box.x + box.w - 4);
  });

  it("fitRowNames:能放下时不动字号", () => {
    expect(fitRowNames([{ cx: 50, name: "萨卡" }, { cx: 200, name: "厄德高" }], { left: 0, right: 300, basePx: 17, measure })).toEqual({ px: 17, texts: ["萨卡", "厄德高"] });
  });
});

describe("xG 赛跑图末端数值标签错开", () => {
  it("回归:手机端 1:1 的 1.57 / 1.25 纵向距离小于字高时上下错开,较大值在上", () => {
    // 1350 版 xG 卡:绘图区高 86px,ymax = 1.57 × 1.1
    const cy0 = 50;
    const cy1 = 136;
    const py = (v: number) => cy1 - (v / (1.57 * 1.1)) * (cy1 - cy0);
    const minGap = 20 * 1.15;
    const y: [number, number] = [py(1.25), py(1.57)];
    expect(Math.abs(y[0] - y[1])).toBeLessThan(minGap);
    const out = separateEndLabels(y, minGap, cy0, cy1);
    expect(out[0] - out[1]).toBeGreaterThanOrEqual(minGap - 1e-9);
    expect(out[1]).toBeGreaterThanOrEqual(cy0);
    expect(out[0]).toBeLessThanOrEqual(cy1);
  });
  it("相距足够时不动;数值相等时主队在上;贴边时不越界", () => {
    expect(separateEndLabels([100, 40], 20, 0, 200)).toEqual([100, 40]);
    const eq = separateEndLabels([80, 80], 20, 0, 200);
    expect(eq[0]).toBeLessThan(eq[1]);
    const edge = separateEndLabels([3, 5], 20, 0, 200);
    expect(Math.min(...edge)).toBeGreaterThanOrEqual(0);
  });
});

describe("分享图阵容左右方向", () => {
  it("左后卫卡拉菲奥里在左、右后卫廷伯在右;右边锋萨卡在右", () => {
    const { L, box } = homeBox(1350);
    const out = lineupLayout(ARSENAL, box, L.s, measure);
    const cx = (name: string) => out[ARSENAL.findIndex((p) => p.name === name)].cx;
    expect(cx("卡拉菲奥里")).toBeLessThan(cx("加布里埃尔"));
    expect(cx("加布里埃尔")).toBeLessThan(cx("孔萨"));
    expect(cx("孔萨")).toBeLessThan(cx("廷伯"));
    expect(cx("萨卡")).toBeGreaterThan(cx("佐利斯"));
  });
});
