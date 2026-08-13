/**
 * 事件 tab:按 event_index 顺序的赛事时间线(进球/牌/换人/VAR…)。
 * Half/AddedTime 是标记不是事件,渲染成全宽分隔条;overload_time 的事件
 * 显示补时角标(如 45+2')。纯展示,无取数,随导入方环境走(不写 use client)。
 */

import type { MatchReportResponse } from "@/lib/api-v1";
import { CARD_ZH, MATCH_EVENT_ZH } from "@/components/matches/zh";
import pageStyles from "@/app/matches/[matchId]/match-detail.module.css";
import styles from "./MatchEventsSection.module.css";

type MatchReport = Extract<MatchReportResponse, { available: true }>;
type ReportEvent = MatchReport["events"][number];

function minuteLabel(e: ReportEvent): string {
  if (e.minute == null) return "—";
  return e.is_added_time ? `${e.minute}+'` : `${e.minute}'`;
}

function EventIcon({ e }: { e: ReportEvent }) {
  if (e.event_type === "Goal") return <span className={styles.iconGoal}>⚽</span>;
  if (e.event_type === "Card") {
    const red = e.card_type === "Red" || e.card_type === "YellowRed";
    return <span className={red ? styles.cardRed : styles.cardYellow} aria-hidden />;
  }
  if (e.event_type === "Substitution") return <span className={styles.iconSub}>⇄</span>;
  if (e.event_type === "MissedPenalty") return <span className={styles.iconMiss}>✕</span>;
  return <span className={styles.iconDot} aria-hidden />;
}

function eventText(e: ReportEvent): string {
  switch (e.event_type) {
    case "Goal":
      return [
        e.player_name ?? "",
        e.assist_player_name ? `(助攻:${e.assist_player_name})` : "",
      ].filter(Boolean).join(" ");
    case "Card":
      return `${e.player_name ?? ""} ${CARD_ZH[e.card_type ?? ""] ?? e.card_type ?? "牌"}`;
    case "Substitution":
      if (e.sub_in_player_name || e.sub_out_player_name) {
        return `${e.sub_in_player_name ?? "?"} 上 / ${e.sub_out_player_name ?? "?"} 下`;
      }
      return e.player_name ?? "换人";
    case "VAR":
      return `VAR 判罚${e.player_name ? `:${e.player_name}` : ""}`;
    case "MissedPenalty":
      return `${e.player_name ?? ""} 射失点球`;
    default:
      return e.player_name ?? MATCH_EVENT_ZH[e.event_type] ?? e.event_type;
  }
}

export function MatchEventsSection({
  events,
  homeName,
  awayName,
}: {
  events: MatchReport["events"];
  homeName: string;
  awayName: string;
}) {
  return (
    <section className={pageStyles.section}>
      <h2 className={pageStyles.sectionTitle}>赛事时间线</h2>
      {events.length === 0 ? (
        <p className={pageStyles.emptyText}>该场比赛暂无赛事事件数据。</p>
      ) : (
        <ol className={styles.timeline}>
          {events.map((e) => {
            if (e.event_type === "Half") {
              return (
                <li key={e.event_index} className={styles.separator}>
                  半场{e.home_score != null && e.away_score != null
                    ? ` ${e.home_score}–${e.away_score}`
                    : ""}
                </li>
              );
            }
            if (e.event_type === "AddedTime") {
              return (
                <li key={e.event_index} className={styles.separator}>
                  补时 {e.minutes_added ?? "?"} 分钟
                </li>
              );
            }
            const side = e.is_home == null ? "neutral" : e.is_home ? "home" : "away";
            return (
              <li key={e.event_index} className={`${styles.row} ${styles[side]}`}>
                <span className={`${styles.minute} num`}>{minuteLabel(e)}</span>
                <EventIcon e={e} />
                <span className={styles.text}>
                  {eventText(e)}
                  {e.event_type === "Goal" && e.home_score != null && e.away_score != null && (
                    <b className={`${styles.score} num`}>
                      {" "}{e.home_score}–{e.away_score}
                    </b>
                  )}
                </span>
                {side !== "neutral" && (
                  <span className={styles.team}>{e.is_home ? homeName : awayName}</span>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
