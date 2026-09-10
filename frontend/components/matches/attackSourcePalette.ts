/**
 * 「进攻来源拆解」配色与碎片合并——纯函数,不带 `"use client"`(CLAUDE.md
 * §11.4,虽然目前只有客户端组件用它,但按项目既有约定,纯计算一律独立成
 * 不带 client 指令的文件,避免以后有服务端组件需要引用时才发现要拆分)。
 *
 * 2026-09 真实缺陷修复:此前 6 色配色表只覆盖 8 个 Situation 里的 6 个,
 * 任意球(FreeKick)和界外球战术(ThrowInSetPiece)共用同一个兜底灰,且
 * 6 色全部挤在青蓝同色相(深色模式运动战/反击对比度只有 1.04:1)。
 * 新配色 8 类跨色相 + 跨明度,来源见 frontend/app/globals.css 的
 * `--source-*` token 头部注释,合成对比度断言见
 * tests/attack-source-palette.test.ts。
 *
 * 后端 Situation 枚举唯一真源:backend/queries/team_style_preview.py 的
 * `_SITUATION_ZH`(2026-08-14 实测 10 个联赛近一年覆盖 99.4%–100%)。
 */

export type SourceKey =
  | "RegularPlay"
  | "FastBreak"
  | "FromCorner"
  | "SetPiece"
  | "FreeKick"
  | "ThrowInSetPiece"
  | "IndividualPlay"
  | "Penalty";

export const SOURCE_KEYS: SourceKey[] = [
  "RegularPlay",
  "FastBreak",
  "FromCorner",
  "SetPiece",
  "FreeKick",
  "ThrowInSetPiece",
  "IndividualPlay",
  "Penalty",
];

const SOURCE_COLOR_VAR: Record<SourceKey, string> = {
  RegularPlay: "var(--source-regular-play)",
  FastBreak: "var(--source-fast-break)",
  FromCorner: "var(--source-from-corner)",
  SetPiece: "var(--source-set-piece)",
  FreeKick: "var(--source-free-kick)",
  ThrowInSetPiece: "var(--source-throw-in)",
  IndividualPlay: "var(--source-individual-play)",
  Penalty: "var(--source-penalty)",
};

/** 合并桶哨兵 key——不是后端会下发的真实 Situation 枚举值。 */
export const OTHER_KEY = "Other";

/** 已知来源用各自的分类色;未知 key(理论上不该出现,后端枚举已固定)和
 * 合并桶都用 `--ink-3`——中性灰,与全部 8 个分类色可辨识区分开
 * (实测 RGB 加权距离最小 88,同色系合成对比度也都 ≥3:1)。 */
export function colorOf(key: string): string {
  return SOURCE_COLOR_VAR[key as SourceKey] ?? "var(--ink-3)";
}

/** 段内是否放得下中文名——手机上没有 hover,`title` 对触屏用户等于不存在
 * (CLAUDE.md §11.2 移动优先),所以宽度足够时必须直接标文字,不能只靠
 * hover tooltip。15% 是经验阈值:两三个字的中文标签在最窄段里塞得下。 */
export const INLINE_LABEL_MIN_PCT = 15;

/** 合并到"其他"的占比下限——低于此值的段,不管什么颜色都认不出,不是
 * 配色能解决的问题(CLAUDE.md §11.3:窄色块问题要靠合并,不是靠调色)。 */
export const MERGE_THRESHOLD_PCT = 5;

export type SourceLike = { key: string; label: string; shots: number; xg?: number | null };

/**
 * 判定哪些来源该合并进"其他"——**必须同时满足"两条(射门 + xG)都低于阈值"
 * 才合并**,不能只看射门占比:两条成分条要保持相同的分段集合,否则
 * "两条错位看出次数多但质量差"这个模块的核心卖点(design-brief-match-
 * detail-viz.md §4)就废了。
 *
 * `xgComplete=false`(任一来源缺 xG)时,xG 那条本身就整条换成文字说明、
 * 不画分段(见 AttackSourceCard 现有逻辑)——这种情况下只用射门占比判定,
 * 不能拿"数据缺失"和"占比小"混成同一个合并理由。
 *
 * 合并后如果剩下的独立段 < 2 个,不合并(“其他”占了大半条却挡住唯一
 * 有意义的对比,没有意义)。
 */
export function sourcesToMerge<T extends SourceLike>(
  rows: T[],
  { xgComplete }: { xgComplete: boolean },
  threshold: number = MERGE_THRESHOLD_PCT,
): Set<string> {
  const shotsTotal = rows.reduce((s, r) => s + r.shots, 0) || 1;
  const xgTotal = xgComplete ? rows.reduce((s, r) => s + (r.xg ?? 0), 0) || 1 : 0;

  const merge = new Set<string>();
  for (const r of rows) {
    const shotsBelow = (r.shots / shotsTotal) * 100 < threshold;
    if (!shotsBelow) continue;
    if (xgComplete) {
      const xgBelow = ((r.xg ?? 0) / xgTotal) * 100 < threshold;
      if (xgBelow) merge.add(r.key);
    } else {
      merge.add(r.key);
    }
  }
  if (rows.length - merge.size < 2) return new Set();
  return merge;
}

export type BarSegment = {
  key: string;
  label: string;
  value: number;
  /** 合并桶包含的原始来源中文名——用于 title/aria-label 如实说明包含什么,
   * 不做静默丢弃(CLAUDE.md §2.2)。真实来源段这里只有它自己。 */
  memberLabels: string[];
};

/** 把 `rows` 按 `pick` 取值、按 `mergeKeys` 合并成最终要画的分段列表。
 * "其他"桶固定放在最后——两条条形的桶都在同一位置,不随数据抖动。 */
export function buildBarSegments<T extends SourceLike>(
  rows: T[],
  pick: (r: T) => number | null | undefined,
  mergeKeys: Set<string>,
): BarSegment[] {
  const known = rows.filter((r) => pick(r) != null);
  const kept: BarSegment[] = [];
  let otherSum = 0;
  const otherLabels: string[] = [];
  for (const r of known) {
    const v = pick(r) as number;
    if (mergeKeys.has(r.key)) {
      otherSum += v;
      otherLabels.push(r.label);
    } else {
      kept.push({ key: r.key, label: r.label, value: v, memberLabels: [r.label] });
    }
  }
  if (otherLabels.length > 0) {
    kept.push({ key: OTHER_KEY, label: "其他", value: otherSum, memberLabels: otherLabels });
  }
  return kept;
}
