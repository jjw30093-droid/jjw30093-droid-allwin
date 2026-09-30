import { describe, expect, it } from "vitest";
import type { SimEvent } from "@/features/simulator/engine";
import type { ResultSnapshot } from "@/features/simulator/snapshot";
import { verdictOf } from "@/features/simulator/verdict";

// 手机 375 宽:内容区 375 − 2×12(页边)− 2×16(卡片内边距)− 2(边框)= 317px;解读字号 15px。
// 宽度估算:中文与全角标点 = 1 个字宽;数字、字母、冒号、斜杠按 0.6 保守估计(Noto Sans SC 实际约 0.55);空格 0.3。
const LINE_UNITS = 317 / 15;
function units(s: string): number {
  return [...s].reduce((w, ch) => w + (ch === " " ? 0.3 : /[\u0000-\u007f]/.test(ch) ? 0.6 : 1), 0);
}
function lines(s: string): number {
  return Math.ceil(units(s) / LINE_UNITS);
}

function shot(team: 0 | 1, xg: number, tick: number, goal = false): SimEvent {
  return { tick, minute: tick + 1, added: 0, clock: `${tick + 1}'`, team, kind: goal ? "goal" : "shot", xg, playerName: "某球员" } as SimEvent;
}

function snap(opts: {
  score: [number, number];
  xg?: [number, number];
  p?: [number, number, number];
  top?: string[];
  count?: number;
  names?: [string, string];
}): ResultSnapshot {
  const [xh, xa] = opts.xg ?? [1.2, 1.0];
  const [ph, pd, pa] = opts.p ?? [0.45, 0.28, 0.27];
  return {
    teams: [{ name: opts.names?.[0] ?? "阿森纳" }, { name: opts.names?.[1] ?? "曼城" }],
    single: { score: opts.score, events: [shot(0, xh, 10), shot(1, xa, 20)] },
    many: {
      runs: 1000,
      pHome: ph,
      pDraw: pd,
      pAway: pa,
      topScores: (opts.top ?? ["1-1", "2-1", "1-0", "2-0", "0-1"]).map((score) => ({ score, p: 0.1 })),
      upset: { scoreCount: opts.count ?? 50, outcomeShare: 0.3, outcome: "H" },
    },
  } as unknown as ResultSnapshot;
}

describe("一句话解读:句式", () => {
  it("xG 占优却输球", () => {
    const v = verdictOf(snap({ score: [0, 1], xg: [2.1, 0.6], p: [0.6, 0.25, 0.15], count: 40 }));
    expect(v.kind).toBe("xg_reversal");
    expect(v.text).toBe("阿森纳 xG 2.1:0.6 占优，却以 0:1 告负；出现 40/1000 次（第 5 常见）。");
  });

  it("爆冷:赢方赛前胜率 < 30%", () => {
    const v = verdictOf(snap({ score: [1, 2], xg: [1.0, 1.4], p: [0.62, 0.2, 0.18], count: 31 }));
    expect(v.kind).toBe("upset");
    expect(v.text).toBe("爆冷！胜率仅 18% 的曼城 2:1 击败阿森纳；出现 31/1000 次（少见）。");
  });

  it("大比分:净胜 ≥ 3", () => {
    const v = verdictOf(snap({ score: [4, 0], xg: [3.2, 0.4], count: 12 }));
    expect(v.kind).toBe("big_win");
    expect(v.text).toBe("阿森纳 4:0 大胜曼城，净胜 4 球；出现 12/1000 次（罕见）。");
  });

  it("最常见的比分(胜)", () => {
    const v = verdictOf(snap({ score: [2, 1], top: ["2-1", "1-1"], count: 131 }));
    expect(v.kind).toBe("most_common");
    expect(v.text).toBe("阿森纳 2:1 胜曼城，正是最常见的比分；出现 131/1000 次（常见）。");
  });

  it("最常见的比分(平)", () => {
    const v = verdictOf(snap({ score: [1, 1], top: ["1-1", "2-1"], count: 131 }));
    expect(v.kind).toBe("most_common");
    expect(v.text).toContain("战平曼城，正是最常见的比分");
  });

  it("平局", () => {
    const v = verdictOf(snap({ score: [2, 2], p: [0.45, 0.28, 0.27], count: 71 }));
    expect(v.kind).toBe("draw");
    expect(v.text).toBe("阿森纳 2:2 战平曼城，平局概率 28%；出现 71/1000 次（少见）。");
  });

  it("普通胜利:给出赛前胜率与比分排名", () => {
    const v = verdictOf(snap({ score: [1, 0], top: ["1-1", "2-1", "1-0"], count: 85 }));
    expect(v.kind).toBe("win");
    expect(v.text).toBe("阿森纳 1:0 战胜曼城，赛前胜率 45%；出现 85/1000 次（第 3 常见）。");
  });

  it("优先级:xG 反转 > 爆冷 > 大比分 > 最常见", () => {
    // 客队胜率低且 xG 反转:按 xG 反转
    expect(verdictOf(snap({ score: [0, 1], xg: [2.0, 0.5], p: [0.7, 0.2, 0.1] })).kind).toBe("xg_reversal");
    // 爆冷且大比分:按爆冷
    expect(verdictOf(snap({ score: [0, 3], xg: [0.8, 2.5], p: [0.7, 0.2, 0.1] })).kind).toBe("upset");
    // 大比分且最常见:按大比分
    expect(verdictOf(snap({ score: [3, 0], xg: [2.5, 0.3], top: ["3-0"] })).kind).toBe("big_win");
    // xG 差距不足 0.3 不算反转
    expect(verdictOf(snap({ score: [0, 1], xg: [1.2, 1.0], p: [0.4, 0.3, 0.3] })).kind).toBe("win");
  });
});

describe("一句话解读:措辞与长度", () => {
  const LONG: [string, string] = ["巴黎圣日耳曼", "埃尔沃斯贝格"];
  const cases = [
    snap({ score: [0, 1], xg: [2.14, 0.61], p: [0.6, 0.25, 0.15], count: 999, names: LONG }),
    snap({ score: [1, 2], xg: [1.0, 1.4], p: [0.62, 0.2, 0.18], count: 999, names: LONG }),
    snap({ score: [10, 0], xg: [3.2, 0.4], count: 999, names: LONG }),
    snap({ score: [2, 1], top: ["2-1"], count: 999, names: LONG }),
    snap({ score: [3, 3], p: [0.45, 0.28, 0.27], count: 999, names: LONG }),
    snap({ score: [2, 3], xg: [1.2, 1.3], p: [0.3, 0.2, 0.5], top: ["1-1", "2-1", "1-0", "2-0", "2-3"], count: 999, names: LONG }),
  ];

  it("最长队名 + 最宽数字时,各句式在手机 375 宽下不超过两行", () => {
    for (const s of cases) {
      const { text, kind } = verdictOf(s);
      expect(lines(text), `${kind}:${text}(${units(text).toFixed(1)} 字宽)`).toBeLessThanOrEqual(2);
    }
  });

  it("不出现'预测''推荐'等字眼", () => {
    for (const s of cases) expect(verdictOf(s).text).not.toMatch(/预测|推荐|稳胆|必胜|投注|下注/);
  });
});
