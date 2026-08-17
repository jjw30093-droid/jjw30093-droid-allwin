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
import { syncStateLabel } from "@/lib/product-status";
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
      {/* 内部字段名(MARKET_BASELINE)、观测点数、统一模糊标签不再逐行输出;
          STALE 保留可行动的醒目提示;UNAVAILABLE 曾被这个条件静默吞掉——
          95/95 场 upcoming 比赛全部 UNAVAILABLE 却在列表上完全看不到提示,
          现在两者都可见,但 UNAVAILABLE 走更克制的次要文字样式(见 CSS),
          不做成和 STALE 一样醒目的警告。 */}
      {(match.sync_state === "STALE" || match.sync_state === "UNAVAILABLE") && (
        <span className={styles.syncLine} data-state={match.sync_state}>
          <b>{syncStateLabel(match.sync_state)}</b>
          {match.sync_state === "STALE" && (
            <>
              {match.data_updated_at && (
                <>
                  {" · 更新于 "}
                  <LocalTime iso={match.data_updated_at} />
                </>
              )}
              {` · ${match.next_planned_sync_at ? "等待计划采集" : "正在等待采集恢复"}`}
            </>
          )}
        </span>
      )}
      {/* 赔率覆盖徽标(D8):区分完整走势与两点摘要,不再把两档混为一谈;
          tier 未计算(联赛 fixtures 端点)或 none 时不渲染,保持行的干净。
          full_timeline 且 odds_freshness_state=STALE 时不能无条件宣称
          "完整走势"——那是旧数据,必须带过期限定语和最后观测时间;
          UNAVAILABLE 或字段缺失(旧后端未下发)维持原文案,不在本次范围内。 */}
      {(match.odds_coverage_tier === "full_timeline" ||
        match.odds_coverage_tier === "open_close_only") && (
        <span className={styles.oddsTier} data-tier={match.odds_coverage_tier}>
          {match.odds_coverage_tier === "full_timeline" &&
          match.odds_freshness_state === "STALE" ? (
            <>
              赔率:完整走势(已过期
              {match.odds_last_observed_at && (
                <>
                  {" · 最后观测 "}
                  <LocalTime iso={match.odds_last_observed_at} />
                </>
              )}
              )
            </>
          ) : match.odds_coverage_tier === "full_timeline" ? (
            "赔率:完整走势"
          ) : (
            "赔率:初盘与临场"
          )}
        </span>
      )}
      {match.win_probability && (
        <span className={styles.probRow}>
          <WinProbabilityBar probability={match.win_probability} compact />
        </span>
      )}
    </Link>
  );
}
