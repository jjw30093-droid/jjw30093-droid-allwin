/**
 * 赛程行(server component,首页与 /matches 共用)。
 * 对称三栏:主队右对齐 / 中间比分或比赛日 / 客队左对齐;
 * 未开赛且有已发布预测时,行下方展示免费层最高一项概率。
 */

import Link from "next/link";
import type { MatchSummary } from "@/lib/api-v1";
import { OUTCOME_ZH, STATUS_ZH, pct } from "./zh";
import styles from "./MatchRow.module.css";

export interface FreeTip {
  top_outcome: "home" | "draw" | "away";
  top_probability: number;
}

export function MatchRow({
  match,
  freeTip,
}: {
  match: MatchSummary;
  freeTip?: FreeTip | null;
}) {
  const finished = match.status === "Finish";
  return (
    <Link href={`/matches/${match.match_id}`} className={styles.row}>
      <span className={styles.home}>{match.home.name}</span>
      <span className={styles.center}>
        {finished && match.home_score != null && match.away_score != null ? (
          <span className={`${styles.score} num`}>
            {match.home_score} - {match.away_score}
          </span>
        ) : (
          <span className={`${styles.date} num`}>{match.date_utc}</span>
        )}
        <span className={styles.status}>
          {STATUS_ZH[match.status] ?? match.status}
          {match.round ? ` · 第${match.round}轮` : ""}
        </span>
      </span>
      <span className={styles.away}>{match.away.name}</span>
      {freeTip && (
        <span className={styles.tip}>
          模型最高概率:{OUTCOME_ZH[freeTip.top_outcome]}{" "}
          <b className="num">{pct(freeTip.top_probability)}</b>
          <span className={styles.tipNote}>(免费层仅展示最高一项)</span>
        </span>
      )}
    </Link>
  );
}
