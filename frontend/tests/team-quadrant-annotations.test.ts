/**
 * 象限图"有结论"(2026-09-26):自动标注 3 个点、标题下一句结论、小样本提示。
 * 文字全部由数据生成——这里用与真实球队无关的假数据,证明没有任何球队名被写死。
 */

import { describe, expect, it } from "vitest";
import {
  ANNOTATION_BADGES,
  SMALL_SAMPLE_MATCHES,
  buildConclusion,
  isSmallSample,
  mean,
  pickAnnotations,
  quadrantOf,
  dirsOf,
  viewById,
  type Pt,
} from "@/components/league/quadrantViews";

const pt = (key: string, x: number, y: number, mp: number | null = 5): Pt => ({
  key, name: `队${key}`, x, y, mp, crestUrl: null, teamId: null,
});

// 攻防视角:x=预期进球(越大越好),y=预期失球(越小越好)
const view = viewById("both-ends");
const pts: Pt[] = [
  pt("A", 2.4, 0.8), // 创造最多 + 被创造最少 → 同时命中两项
  pt("B", 1.0, 1.9),
  pt("C", 1.5, 1.5),
  pt("D", 1.2, 1.2),
  pt("E", 2.0, 1.8),
  pt("F", 0.2, 3.6), // 离平均最远(差的方向)
];
const mx = mean(pts.map((p) => p.x));
const my = mean(pts.map((p) => p.y));

describe("pickAnnotations", () => {
  const ann = pickAnnotations(pts, view, mx, my);

  it("x 轴最好 = 最大值;y 轴 lowerIsBetter,最好 = 最小值(不是最大)", () => {
    const all = ann.flatMap((a) => a.reasons.map((r) => [a.key, r] as const));
    expect(all.find(([, r]) => r.includes("场均创造的机会"))?.[0]).toBe("A");
    expect(all.find(([, r]) => r.includes("场均被对手创造的机会"))?.[0]).toBe("A");
    expect(all.find(([, r]) => r.includes("联赛最高"))).toBeTruthy();
    expect(all.find(([, r]) => r.includes("联赛最低"))).toBeTruthy();
  });

  it("同一队命中多项时合并成一条,序号连续从 1 起", () => {
    // A 命中 x 最好 + y 最好;F 命中"离平均最远" → 共 2 条,而不是 3 条
    expect(ann.map((a) => a.key)).toEqual(["A", "F"]);
    expect(ann.map((a) => a.no)).toEqual([1, 2]);
    expect(ann[0].reasons).toHaveLength(2);
  });

  it("文字里的数值与数据一致(用轴的 digits/unit 格式化),队名取自数据", () => {
    const a = ann.find((x) => x.key === "A")!;
    expect(a.name).toBe("队A");
    expect(a.reasons.join("；")).toContain("2.40");
    expect(a.reasons.join("；")).toContain("0.80");
  });

  it("离平均最远的队,象限名与 quadrantOf 一致", () => {
    const f = ann.find((x) => x.key === "F")!;
    const q = view.quadrants[quadrantOf(pts[5], mx, my, dirs(view))];
    expect(f.reasons.join("")).toContain(`「${q}」`);
    expect(q).toBe("两头都弱");
  });

  it("三项各是不同的队时,输出 3 条,序号徽标数量够用", () => {
    const three = [pt("A", 3, 1.6), pt("B", 1.5, 0.5), pt("C", 1, 1.5), pt("D", 1.2, 1.6), pt("E", 0.4, 2.8)];
    const m1 = mean(three.map((p) => p.x));
    const m2 = mean(three.map((p) => p.y));
    const r = pickAnnotations(three, view, m1, m2);
    expect(r.map((a) => a.key).sort()).toEqual(["A", "B", "E"]);
    expect(r.length).toBeLessThanOrEqual(ANNOTATION_BADGES.length);
  });

  it("点数不足 2 时不标注", () => {
    expect(pickAnnotations([pt("A", 1, 1)], view, 1, 1)).toEqual([]);
  });

  it("结果确定:同输入两次调用完全一致", () => {
    expect(pickAnnotations(pts, view, mx, my)).toEqual(pickAnnotations(pts, view, mx, my));
  });
});

describe("buildConclusion", () => {
  it("一句话:最远的队 + 它的象限 + 各象限球队数(合计 = 画出的球队数)", () => {
    const text = buildConclusion(pts, view, mx, my);
    expect(text).toContain("队F综合离联赛平均最远，属于「两头都弱」");
    const counts = [...text.matchAll(/(\d+) 队/g)].map((m) => Number(m[1]));
    expect(counts).toHaveLength(4);
    expect(counts.reduce((a, b) => a + b, 0)).toBe(pts.length);
    expect(text.endsWith("。")).toBe(true);
    expect(text.match(/。/g)).toHaveLength(1);
  });

  it("筛选状态下均值线叫法随窗口变化,不能继续叫联赛平均", () => {
    expect(buildConclusion(pts, view, mx, my, "最近5场平均")).toContain("离最近5场平均最远");
  });

  it("空点集返回空串", () => {
    expect(buildConclusion([], view, 0, 0)).toBe("");
  });
});

describe("isSmallSample", () => {
  it("最少场次 < 10 → 样本较小;恰好 10 → 不提示", () => {
    expect(SMALL_SAMPLE_MATCHES).toBe(10);
    expect(isSmallSample([{ mp: 12 }, { mp: 9 }])).toBe(true);
    expect(isSmallSample([{ mp: 10 }, { mp: 11 }])).toBe(false);
  });

  it("场次全部缺失时不下判断(不能把'不知道'当成'很小')", () => {
    expect(isSmallSample([{ mp: null }, { mp: null }])).toBe(false);
    expect(isSmallSample([])).toBe(false);
  });
});

function dirs(v: typeof view) {
  return dirsOf(v);
}
