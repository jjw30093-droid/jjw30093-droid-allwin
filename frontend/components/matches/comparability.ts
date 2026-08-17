/**
 * 验收返工三(独立复核第二轮再收紧,P1):fallback 分级窗口
 * (`venue_full`/`venue_partial`/`mixed`/`unavailable`)之间口径不同,
 * 不能被前端直接拿来比较。
 *
 * 只有**精确同一个 tier** 才可比:
 * - `venue_full` vs `venue_full`;
 * - `venue_partial` vs `venue_partial`。
 *
 * 以下一律不可比(此前的宽松版本把 venue_full/venue_partial 视为"同一
 * 口径"、把 mixed/mixed 也视为可比,均已被独立复核明确否决——
 * venue_full 和 venue_partial 虽然都是"该队自己真实同场景历史",但样本
 * 量本身不同,不是同一口径;mixed 是"自己样本不够、退回合并主客场"的
 * 近似值,连自己跟自己比都不构成可比,因为两支队伍各自 mixed 的成因、
 * 混入比例都不保证一致):
 * - `venue_full` vs `venue_partial`;
 * - `mixed` vs 任何 tier(含 `mixed` vs `mixed`);
 * - 任何 tier vs `unavailable`。
 *
 * 只用于"要不要生成比较结论/胜负式摘要/关键对位"的判断,不影响原始数字
 * 的展示——两队各自的真实数字始终如实展示,被挡住的只是"谁更高"这句话。
 */

export type WindowTier = "venue_full" | "venue_partial" | "mixed" | "unavailable";

const COMPARABLE_TIERS = new Set<string>(["venue_full", "venue_partial"]);

export function tiersComparable(a: string, b: string): boolean {
  if (!COMPARABLE_TIERS.has(a) || !COMPARABLE_TIERS.has(b)) return false;
  return a === b;
}

export const INCOMPARABLE_NOTE = "样本口径不同,暂不作高低判断。";
