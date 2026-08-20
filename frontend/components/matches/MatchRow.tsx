/**
 * 赛程行(server component,首页与 /matches 共用)。
 * 对称三栏:主队右对齐 / 中间比分或比赛日 / 客队左对齐;
 * 未开赛且有赔率时,行下方展示 Bet365 1x2 折算的胜平负概率条。
 */

import Link from "next/link";
import type { MatchSummary } from "@/lib/api-v1";
import { buildMatchHref } from "@/lib/match-links";
import { LocalTime } from "./LocalTime";
import { WinProbabilityBar } from "./WinProbabilityBar";
import { TeamBadge } from "@/components/teams/TeamBadge";
import { STATUS_ZH } from "./zh";
import styles from "./MatchRow.module.css";

/**
 * 免费层最高一项概率的类型定义——保留给 lib/homepage.ts 的既有类型引用用,
 * 渲染逻辑已被下面的 WinProbabilityBar 取代
 * (MatchListLive 此前每场发一个 /prediction 请求算这个字段,实测
 * prediction_snapshots 是 0 行,100% 返回空——纯粹的 N+1,直接删掉请求)。
 */
export interface FreeTip {
  top_outcome: "home" | "draw" | "away";
  top_probability: number;
  probability_source: "MODEL" | "MARKET_BASELINE" | "UNAVAILABLE";
}

export function MatchRow({
  match,
  returnTo,
}: {
  match: MatchSummary;
  returnTo?: string;
}) {
  const finished = match.status === "Finish";
  const detailHref = buildMatchHref(match.match_id, returnTo);
  return (
    <Link href={detailHref} className={styles.row}>
      <span className={styles.home}>
        <span>{match.home.name}</span>
        <TeamBadge
          teamName={match.home.name}
          crestUrl={match.home.crest_url}
          size={40}
        />
      </span>
      <span className={styles.center}>
        {finished && match.home_score != null && match.away_score != null ? (
          <span className={`${styles.score} num`}>
            {match.home_score} - {match.away_score}
          </span>
        ) : (
          <span className={`${styles.date} num`}>
            {match.kickoff_at_utc ? (
              <LocalTime iso={match.kickoff_at_utc} fallback={match.date_utc} />
            ) : (
              match.date_utc
            )}
          </span>
        )}
        <span className={styles.status}>
          {STATUS_ZH[match.status] ?? match.status}
          {match.round ? ` · 第${match.round}轮` : ""}
        </span>
      </span>
      <span className={styles.away}>
        <TeamBadge
          teamName={match.away.name}
          crestUrl={match.away.crest_url}
          size={40}
        />
        <span>{match.away.name}</span>
      </span>
      {/* 2026-08-20 站长要求删除:sync_state("部分数据暂不可用"等)与
          odds_coverage_tier("赔率:完整走势"等)两行内部运维口径文案——两句
          话字面上容易读成互相矛盾("数据不可用"紧跟"赔率完整"),对普通用户
          没有实际信息量,直接从行内移除;字段本身仍在 MatchSummary 里,
          只是这个组件不再消费。 */}
      {match.win_probability && (
        <span className={styles.probRow}>
          <WinProbabilityBar probability={match.win_probability} compact />
        </span>
      )}
    </Link>
  );
}
