"use client";

/**
 * 关键变化(比赛详情页第 6 区块;时间共现,不声称因果)。
 * 区块标题与 <section> 由本组件自带:加载中/加载失败/无内容时整个区块
 * 不渲染("暂无同期事件"对用户没有价值,空标题也不占屏)。
 * 2026-08-16 权限口径修正:后端恒返回完整明细(items 不再是判别联合里
 * "匿名/free 只给计数"的那一支),不再有身份分层,直接渲染。
 * 文案只允许"同期/同时段检测到",禁止任何因果表述(宪法 §6.4)。
 */

import { useCallback, useEffect, useState } from "react";
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
  void retry;

  // 加载中/加载失败/确认无内容:整个区块不渲染(不展示空标题与占位文案)
  if (error || resp == null || resp.count === 0) return null;

  const countLine = (
    <p className={styles.countLine}>
      共 <b className="num">{resp.count}</b> 组同时段变化。
      <b>同一时间窗内观察到两类变化,不代表二者存在因果关系。</b>
    </p>
  );

  return (
    <section className={styles.section}>
      <h2 className={styles.sectionTitle}>
        <span className={styles.sectionBar} aria-hidden />
        关键变化
      </h2>
      {countLine}
      <ul className={styles.list}>
        {resp.items.map((item, i) => {
          const detail = eventDetail(item);
          return (
            <li key={`${item.odds_moved_at}-${i}`} className={styles.item}>
              <div className={styles.itemLine}>
                <span className={styles.itemTime}>
                  <LocalTime iso={item.odds_moved_at} />
                </span>
                <span>本站采集到赔率变化:{describeOddsMove(item)}</span>
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
    </section>
  );
}
