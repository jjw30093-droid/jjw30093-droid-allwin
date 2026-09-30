// 结果页的一句话解读(纯前端,只读本次模拟结果;不改模型与计算)。
// 句子 = 事件分句 + "；" + 稀有度分句 + "。",手机 375 宽下不超过两行(见 tests/simulator-verdict.test.ts)。
// 不使用"预测""推荐"等字眼。

import { rarityTag } from "./engine";
import type { ResultSnapshot } from "./snapshot";
import { cumulativeXg } from "./xg";

export type VerdictKind = "xg_reversal" | "upset" | "big_win" | "most_common" | "draw" | "win";

/** xG 与比分方向相反时,输球一方的 xG 至少领先这么多才算"占优" */
export const XG_REVERSAL_MARGIN = 0.3;
/** 赢方赛前胜率低于此值算爆冷 */
export const UPSET_THRESHOLD = 0.3;
/** 净胜几球算大比分 */
export const BIG_MARGIN = 3;

export interface Verdict {
  kind: VerdictKind;
  text: string;
}

const pct = (p: number) => `${Math.round(p * 100)}%`;
const xg1 = (x: number) => x.toFixed(1);

export function verdictOf(snap: ResultSnapshot): Verdict {
  const [h, a] = snap.single.score;
  const names = [snap.teams[0].name, snap.teams[1].name] as const;
  const { many } = snap;
  const xg = cumulativeXg(snap.single.events);
  const key = `${h}-${a}`;
  const rank = many.topScores.findIndex((s) => s.score === key);
  const count = many.upset.scoreCount;
  const rarity = rank >= 1 ? `第 ${rank + 1} 常见` : rarityTag(count, many.runs);
  const tail = `出现 ${count}/${many.runs} 次（${rarity}）`;

  let kind: VerdictKind;
  let head: string;
  if (h === a) {
    if (rank === 0) {
      kind = "most_common";
      head = `${names[0]} ${h}:${a} 战平${names[1]}，正是最常见的比分`;
    } else {
      kind = "draw";
      head = `${names[0]} ${h}:${a} 战平${names[1]}，平局概率 ${pct(many.pDraw)}`;
    }
  } else {
    const w = h > a ? 0 : 1;
    const l = 1 - w;
    const ws = Math.max(h, a);
    const ls = Math.min(h, a);
    const winP = w === 0 ? many.pHome : many.pAway;
    if (xg[l] - xg[w] >= XG_REVERSAL_MARGIN) {
      kind = "xg_reversal";
      head = `${names[l]} xG ${xg1(xg[l])}:${xg1(xg[w])} 占优，却以 ${ls}:${ws} 告负`;
    } else if (winP < UPSET_THRESHOLD) {
      kind = "upset";
      head = `爆冷！胜率仅 ${pct(winP)} 的${names[w]} ${ws}:${ls} 击败${names[l]}`;
    } else if (ws - ls >= BIG_MARGIN) {
      kind = "big_win";
      head = `${names[w]} ${ws}:${ls} 大胜${names[l]}，净胜 ${ws - ls} 球`;
    } else if (rank === 0) {
      kind = "most_common";
      head = `${names[w]} ${ws}:${ls} 胜${names[l]}，正是最常见的比分`;
    } else {
      kind = "win";
      head = `${names[w]} ${ws}:${ls} 战胜${names[l]}，赛前胜率 ${pct(winP)}`;
    }
  }
  return { kind, text: `${head}；${tail}。` };
}
