/**
 * 比赛详情页头部 · 赛前/进行中版式(2026-08-14 重设计,Claude Design 定稿)。
 * 已完赛见 MatchHeaderFinished.tsx——两套版式内容差异较大(VS vs 比分、
 * 关注按钮位置、日期行内容),拆成独立组件比在一个组件里塞双分支更清楚;
 * 深浅色仍是单份 JSX 只换 CSS 变量,不违反 DESIGN.md §2.9 的铁律
 * (铁律禁止的是"同一份内容因主题分两套 JSX",不是"两种不同内容分两个组件")。
 */

import { FollowButton } from "@/components/matches/FollowButton";
import { KickoffCountdown } from "@/components/matches/KickoffCountdown";
import { LeagueBadge } from "@/components/matches/LeagueBadge";
import { LocalTime } from "@/components/matches/LocalTime";
import { TeamBadge } from "@/components/teams/TeamBadge";
import { LEAGUE_ZH, STATUS_ZH } from "@/components/matches/zh";
import type { MatchSummary } from "@/lib/api-v1";
import styles from "./MatchHeaderPre.module.css";

export function MatchHeaderPre({ match }: { match: MatchSummary }) {
  return (
    <header className={styles.card}>
      <div className={styles.metaRow}>
        <span className={styles.league}>
          <LeagueBadge leagueId={match.league_id} size={16} />
          {LEAGUE_ZH[match.league_id] ?? `联赛 ${match.league_id}`}
          {match.round ? ` · 第${match.round}轮` : ""}
        </span>
        <span className={styles.badges}>
          <span className={styles.statusPill}>{STATUS_ZH[match.status] ?? match.status}</span>
          {match.sync_state === "STALE" && (
            <span className={styles.stalePill}>数据已过期</span>
          )}
          <FollowButton matchId={match.match_id} />
        </span>
      </div>

      <div className={styles.matchup}>
        <span className={styles.homeSide}>
          <span className={styles.teamName}>{match.home.name}</span>
          <span className={styles.crestFrame}>
            <TeamBadge teamName={match.home.name} crestUrl={match.home.crest_url} size={40} eager />
          </span>
        </span>
        <span className={styles.vs}>VS</span>
        <span className={styles.awaySide}>
          <span className={styles.crestFrame}>
            <TeamBadge teamName={match.away.name} crestUrl={match.away.crest_url} size={40} eager />
          </span>
          <span className={styles.teamName}>{match.away.name}</span>
        </span>
      </div>

      <div className={styles.kickoffRow}>
        {match.kickoff_at_utc ? (
          <>
            <span className={styles.kickoffTime}>
              <LocalTime iso={match.kickoff_at_utc} fallback={match.date_utc} />
            </span>
            <span className={styles.countdown}>
              北京时间 · <KickoffCountdown iso={match.kickoff_at_utc} variant="prefix" />
            </span>
          </>
        ) : (
          <span className={styles.kickoffTime}>{match.date_utc}</span>
        )}
      </div>
    </header>
  );
}
