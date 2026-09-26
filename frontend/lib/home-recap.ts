/**
 * 首页「今日精选」未发布时的复盘文案(2026-09-26 第三批):
 * 取最近一次**已结算**的精选,输出 日期 / 比赛 / 结果 三段。
 *
 * 数据来自公开的 /api/v1/reco/track-record(匿名可见的站点运营记录,不属于每日精选
 * 按场授权面)。作废单不算"已结算",不进复盘。纯函数,没有任何 "use client" 依赖。
 */

import type { GetJson } from "@/lib/api-v1";

type TrackRecord = GetJson<"/api/v1/reco/track-record">;
type Slip = TrackRecord["slips"][number];

export type RecapTone = "win" | "neutral";

export type RecapSlip = {
  dateText: string;
  matchText: string;
  resultText: string;
  tone: RecapTone;
};

// 措辞与 components/reco/TrackRecordPanel.tsx 的 RESULT_ZH 一致
const RESULT_ZH: Record<string, string> = {
  win: "命中",
  lose: "未中",
  push: "走水",
  half_win: "半赢",
  half_loss: "半输",
};

/** "2026-09-14" → "9月14日";格式不对时原样返回,不猜。 */
export function recapDateText(slipDate: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(slipDate);
  return m ? `${Number(m[2])}月${Number(m[3])}日` : slipDate;
}

/** match_desc 末尾带着"MM/DD HH:mm"开球时间("科莫 vs 帕尔马 09/15 00:30"),复盘里只要队名。 */
export function recapMatchName(desc: string): string {
  return desc.replace(/\s+\d{2}\/\d{2}\s+\d{2}:\d{2}\s*$/, "").trim();
}

export function pickLatestSettledSlip(slips: readonly Slip[]): RecapSlip | null {
  const settled = slips
    .filter((s) => s.status === "settled" && !!s.result && s.result in RESULT_ZH)
    .sort((a, b) => {
      const byDate = b.slip_date.localeCompare(a.slip_date);
      if (byDate !== 0) return byDate;
      return (b.settled_at ?? "").localeCompare(a.settled_at ?? "");
    });
  const slip = settled[0];
  if (!slip) return null;
  const legs = slip.legs ?? [];
  const first = legs[0]?.match_desc ? recapMatchName(legs[0].match_desc) : slip.title;
  const matchText = legs.length > 1 ? `${first} 等 ${legs.length} 场` : first;
  return {
    dateText: recapDateText(slip.slip_date),
    matchText,
    resultText: RESULT_ZH[slip.result as string],
    tone: slip.result === "win" || slip.result === "half_win" ? "win" : "neutral",
  };
}
