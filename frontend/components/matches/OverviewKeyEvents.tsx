"use client";

/**
 * 总览「关键事件」精简时间线(2026-08-25):只列进球与红黄牌,底部跳转
 * 「事件」tab 看完整时间线——与专门的事件 tab 职能不重叠(那边有换人/VAR/
 * 半场分隔的全量),不是同一块内容抄两份。
 *
 * 乌龙球:fact_match_events 的 is_own_goal 事件其 is_home 指向**受益方**
 * (全库 1066/1066 核验,见 backend/queries/match_report.py::_events 注释),
 * 这里按受益方侧展示 + 「(乌龙)」标注,与比分演进一致。
 */

import { CARD_ZH } from "@/components/matches/zh";
import { isKeyEvent, type MatchReportEvent } from "./overviewKeyEvents.shared";
import { useMatchTabSwitch } from "./MatchTabs";
import styles from "./OverviewKeyEvents.module.css";

type MatchEvent = MatchReportEvent;

function minuteText(e: MatchEvent): string {
  if (e.minute == null) return "";
  return e.is_added_time && e.minutes_added != null
    ? `${e.minute}+${e.minutes_added}'`
    : `${e.minute}'`;
}

function eventIcon(e: MatchEvent): string {
  if (e.event_type === "Goal") return "⚽";
  if (e.card_type === "Red" || e.card_type === "YellowRed") return "🟥";
  return "🟨";
}

export function OverviewKeyEvents({ events }: { events: MatchEvent[] }) {
  const switchTab = useMatchTabSwitch();
  const keyEvents = events.filter(isKeyEvent);
  if (keyEvents.length === 0) return null;

  return (
    <div className={styles.card} data-testid="overview-key-events">
      <ul className={styles.list}>
        {keyEvents.map((e) => (
          <li
            key={e.event_index}
            className={styles.row}
            data-side={e.is_home == null ? "none" : e.is_home ? "home" : "away"}
          >
            <span className={`${styles.minute} num`}>{minuteText(e)}</span>
            <span className={styles.icon} aria-hidden>
              {eventIcon(e)}
            </span>
            <span className={styles.body}>
              {e.player_name ?? ""}
              {e.event_type === "Goal" && e.is_own_goal && (
                <span className={styles.ownGoal}>(乌龙)</span>
              )}
              {e.event_type === "Card" && e.card_type && (
                <span className={styles.cardKind}>{CARD_ZH[e.card_type] ?? e.card_type}</span>
              )}
            </span>
            {e.event_type === "Goal" && e.home_score != null && e.away_score != null && (
              <span className={`${styles.score} num`}>
                {e.home_score}–{e.away_score}
              </span>
            )}
          </li>
        ))}
      </ul>
      {switchTab && (
        <button
          type="button"
          className={styles.moreLink}
          onClick={() => switchTab("events")}
        >
          查看完整时间线 →
        </button>
      )}
    </div>
  );
}
