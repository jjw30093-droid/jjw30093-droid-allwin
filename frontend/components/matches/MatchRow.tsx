/**
 * 赛程行(server component,首页与 /matches 共用)。
 * 对称三栏:主队右对齐 / 中间比分或比赛日 / 客队左对齐;
 * 未开赛且有已发布预测时,行下方展示免费层最高一项概率。
 */

import Link from "next/link";
import type { MatchSummary } from "@/lib/api-v1";
import { buildMatchHref } from "@/lib/match-links";
import { LocalTime } from "./LocalTime";
import { TeamBadge } from "@/components/teams/TeamBadge";
import { syncStateLabel } from "@/lib/product-status";
import { OUTCOME_ZH, STATUS_ZH, pct } from "./zh";
import styles from "./MatchRow.module.css";

export interface FreeTip {
  top_outcome: "home" | "draw" | "away";
  top_probability: number;
  probability_source: "MODEL" | "MARKET_BASELINE" | "UNAVAILABLE";
}

export function MatchRow({
  match,
  freeTip,
  returnTo,
}: {
  match: MatchSummary;
  freeTip?: FreeTip | null;
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
          size={32}
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
          size={32}
        />
        <span>{match.away.name}</span>
      </span>
      {match.sync_state && (
        <span className={styles.syncLine} data-state={match.sync_state}>
          <b>{syncStateLabel(match.sync_state)}</b>
          {match.data_updated_at && (
            <>
              {" · 更新 "}
              <LocalTime iso={match.data_updated_at} />
            </>
          )}
          {match.probability_source && ` · ${match.probability_source}`}
          {match.odds_observation_count != null &&
            ` · 赔率观测 ${match.odds_observation_count} 个点`}
          {match.sync_state === "STALE" &&
            ` · ${
              match.next_planned_sync_at
                ? "数据已过期，等待计划采集"
                : "数据已过期，正在等待采集恢复"
            }`}
        </span>
      )}
      {/* 赔率覆盖徽标(D8):区分完整走势与两点摘要,不再把两档混为一谈;
          tier 未计算(联赛 fixtures 端点)或 none 时不渲染,保持行的干净 */}
      {(match.odds_coverage_tier === "full_timeline" ||
        match.odds_coverage_tier === "open_close_only") && (
        <span className={styles.oddsTier} data-tier={match.odds_coverage_tier}>
          {match.odds_coverage_tier === "full_timeline"
            ? "赔率:完整走势"
            : "赔率:初盘与临场"}
        </span>
      )}
      {freeTip && (
        <span className={styles.tip}>
          {freeTip.probability_source === "MARKET_BASELINE"
            ? "市场去水基线"
            : "模型最高概率"}
          :{OUTCOME_ZH[freeTip.top_outcome]}{" "}
          <b className="num">{pct(freeTip.top_probability)}</b>
          <span className={styles.tipNote}>(免费分析仅展示最高一项)</span>
        </span>
      )}
    </Link>
  );
}
