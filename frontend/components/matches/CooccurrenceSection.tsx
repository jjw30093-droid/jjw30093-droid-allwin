"use client";

/**
 * 同期事件(比赛详情页第 6 区块;时间共现,不声称因果)。
 * 匿名/Free/Pro(无 report:deep):后端只给计数;Pro 深度报告权益给明细。
 * 文案只允许"同期/同时段检测到",禁止任何因果表述(宪法 §6.4)。
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { clientFetch } from "@/lib/api-v1";
import { LocalTime } from "./LocalTime";
import { EVENT_TYPE_ZH, MARKET_ZH } from "./zh";
import type { CooccurrenceItem, CooccurrenceResponse } from "./types";
import styles from "./CooccurrenceSection.module.css";

function describeOddsMove(item: CooccurrenceItem): string {
  const market = MARKET_ZH[item.market] ?? item.market;
  const prev = item.prev_value != null ? String(item.prev_value) : "—";
  const next = item.new_value != null ? String(item.new_value) : "—";
  return `${market} ${item.field}:${prev} → ${next}`;
}

function eventDetail(item: CooccurrenceItem): string | null {
  if (!item.detail_json) return null;
  try {
    const parsed: unknown = JSON.parse(item.detail_json);
    if (parsed && typeof parsed === "object") {
      const text = JSON.stringify(parsed);
      return text === "{}" ? null : text;
    }
    return null;
  } catch {
    return null;
  }
}

export function CooccurrenceSection({ matchId }: { matchId: number }) {
  const [resp, setResp] = useState<CooccurrenceResponse | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    clientFetch<CooccurrenceResponse>(`/api/v1/matches/${matchId}/cooccurrence`)
      .then((d) => {
        if (!cancelled) setResp(d);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [matchId, attempt]);

  const retry = useCallback(() => {
    setError(false);
    setAttempt((n) => n + 1);
  }, []);

  if (error) {
    return (
      <div className={styles.stateBox}>
        同期事件数据加载失败。
        <button type="button" onClick={retry} className={styles.retryBtn}>
          重试
        </button>
      </div>
    );
  }
  if (resp == null) {
    return (
      <div className={styles.skeleton} aria-label="同期事件加载中">
        <span className={styles.skelLine} />
        <span className={styles.skelLine} />
      </div>
    );
  }
  if (resp.count === 0) {
    return (
      <div className={styles.stateBox}>
        暂无同期事件:系统未在固定时间窗内同时检测到赔率变化与阵容/伤停变化。
      </div>
    );
  }
  if (resp.items == null) {
    return (
      <div className={styles.stateBox}>
        系统共检测到 <b className="num">{resp.count}</b> 组同期事件
        (固定时间窗内,赔率变化与阵容/伤停变化在同一时段出现)。
        <p className={styles.lockNote}>
          明细为 Pro 深度报告内容。
          <Link href="/pricing" className={styles.lockLink}>
            了解会员权益 →
          </Link>
        </p>
      </div>
    );
  }

  return (
    <div>
      <p className={styles.countLine}>
        共 <b className="num">{resp.count}</b>{" "}
        组同期事件。以下为时间共现记录:同一时间窗内观察到两类变化,不代表二者存在因果关系。
      </p>
      <ul className={styles.list}>
        {resp.items.map((item, i) => {
          const detail = eventDetail(item);
          return (
            <li key={`${item.odds_moved_at}-${i}`} className={styles.item}>
              <div className={styles.itemLine}>
                <span className={styles.itemTime}>
                  <LocalTime iso={item.odds_moved_at} />
                </span>
                <span>系统检测到赔率变化:{describeOddsMove(item)}</span>
              </div>
              <div className={styles.itemLine}>
                <span className={styles.itemTime}>
                  <LocalTime iso={item.event_moved_at} />
                </span>
                <span>
                  同时段检测到:
                  {EVENT_TYPE_ZH[item.event_type] ?? item.event_type}
                  (相差 <span className="num">{Math.abs(item.delta_seconds)}</span>{" "}
                  秒,时间窗 <span className="num">{item.window_seconds}</span> 秒)
                </span>
              </div>
              {detail && <div className={styles.detail}>{detail}</div>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
