/**
 * 比赛详情页头部 · 已完赛版式(2026-08-14 重设计,Claude Design 定稿)。
 * 赛前/进行中见 MatchHeaderPre.tsx。
 */

import { FollowButton } from "@/components/matches/FollowButton";
import { LeagueBadge } from "@/components/matches/LeagueBadge";
import { LocalTime } from "@/components/matches/LocalTime";
import { TeamBadge } from "@/components/teams/TeamBadge";
import { LEAGUE_ZH, matchDayZh } from "@/components/matches/zh";
import type { MatchDetailResponse } from "@/lib/api-v1";
import styles from "./MatchHeaderFinished.module.css";

export function MatchHeaderFinished({ detail }: { detail: MatchDetailResponse }) {
  const match = detail.match;
  const day = matchDayZh(match.kickoff_at_utc, match.date_utc);

  return (
    <header className={styles.card}>
      <div className={styles.metaRow}>
        <span className={styles.league}>
          <LeagueBadge leagueId={match.league_id} size={16} />
          {LEAGUE_ZH[match.league_id] ?? `联赛 ${match.league_id}`}
          {match.round ? ` · 第${match.round}轮` : ""}
        </span>
        <span className={styles.badges}>
          <span className={styles.finishedPill}>已完赛</span>
          {match.sync_state === "STALE" && (
            <span className={styles.stalePill}>数据已过期</span>
          )}
          <FollowButton matchId={match.match_id} />
        </span>
      </div>

      <p className={styles.score}>
        {match.home_score}–{match.away_score}
      </p>

      <div className={styles.matchup}>
        <span className={styles.homeSide}>
          <span className={styles.teamName}>{match.home.name}</span>
          <span className={styles.crestFrame}>
            <TeamBadge teamName={match.home.name} crestUrl={match.home.crest_url} size={36} eager />
          </span>
        </span>
        <span className={styles.full}>全场</span>
        <span className={styles.awaySide}>
          <span className={styles.crestFrame}>
            <TeamBadge teamName={match.away.name} crestUrl={match.away.crest_url} size={36} eager />
          </span>
          <span className={styles.teamName}>{match.away.name}</span>
        </span>
      </div>

      <p className={styles.dateLine}>
        {match.season} 赛季 · {day.text}{day.isBeijing ? "(北京时间)" : "(UTC 日期)"}
        {detail.data_updated_at && (
          <>
            {" · 数据更新于 "}
            <LocalTime iso={detail.data_updated_at} />
          </>
        )}
      </p>
    </header>
  );
}
