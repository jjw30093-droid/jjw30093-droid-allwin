/**
 * 比赛详情「数据倾向」卡的展示门槛(2026-10-01 站长 P0)。
 *
 * 面向普通竞彩用户,只展示"有明确方向、且历史上这个方向对得多于错"的卡:
 * - data_quality 为 ok、有星级(signal_grade)、有方向(lean);
 * - 历史命中率存在且 ≥ 50%。
 * 「未标定」「样本不足」「样本外不稳定」以及命中率不到一半的卡一律不显示——
 * 此前页面上出现过"偏小 · 历史命中率 48%",等于自己告诉用户这个判断不太准。
 *
 * 纯函数,不带 "use client"(服务端/客户端都可 import,§11.4)。
 */
import type { MatchMarketCardsResponse } from "@/lib/api-v1";

export type MarketCardData = MatchMarketCardsResponse["cards"][number];

export const MIN_SHOWN_HIT_RATE = 0.5;

export function isShowableMarketCard(card: MarketCardData): boolean {
  return (
    card.data_quality === "ok" &&
    card.signal_grade != null &&
    card.lean != null &&
    card.hit_rate != null &&
    card.hit_rate >= MIN_SHOWN_HIT_RATE
  );
}
