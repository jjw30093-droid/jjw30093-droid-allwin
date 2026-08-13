import type { PredictionResponse } from "./api-v1";
import type { FreeTip } from "@/components/matches/MatchRow";

/**
 * 免费层最高一项概率投影 —— SSR(匿名)与浏览器端会员刷新共用同一份判据,
 * 避免"服务端一套、客户端一套"的二次实现随时间漂移。
 */
export function freeTipOf(resp: PredictionResponse | null): FreeTip | null {
  if (!resp?.available || !resp.prediction) return null;
  const p = resp.prediction;
  if (p.tier === "free") {
    return {
      top_outcome: p.top_outcome,
      top_probability: p.top_probability,
      probability_source: p.meta.probability_source,
    };
  }
  return null;
}
