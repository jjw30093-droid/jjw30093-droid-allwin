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
 * 免费层最高一项概率的类型定义——保留给 lib/homepage.ts / lib/free-tip.ts
 * 的既有类型引用用,渲染逻辑已被下面的 WinProbabilityBar 取代
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
          只保留可行动的 STALE 提示与最近更新时间(详情页仍可完整溯源)。 */}
      {match.sync_state === "STALE" && (
        <span className={styles.syncLine} data-state={match.sync_state}>
          <b>{syncStateLabel(match.sync_state)}</b>
          {match.data_updated_at && (
            <>
              {" · 更新于 "}
              <LocalTime iso={match.data_updated_at} />
            </>
          )}
          {` · ${match.next_planned_sync_at ? "等待计划采集" : "正在等待采集恢复"}`}
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
      {match.win_probability && (
        <span className={styles.probRow}>
          <WinProbabilityBar probability={match.win_probability} compact />
        </span>
      )}
      {/* 2026-08-13:本场所属联赛不在当前身份权限内——比赛本身仍在列表里,
          只是概率条不下发(见 MatchSummary.requires_login),用一行提示
          说明原因,点进详情页会走既有登录门禁。 */}
      {match.requires_login && (
        <span className={styles.loginHint}>登录后查看胜平负概率</span>
      )}
    </Link>
  );
}
