"use client";

/**
 * 市场卡列表(比赛详情页「数据倾向」区块,赛前/完赛均可用)。
 * 结构:结论区常驻 + 驱动因子折叠,见 MarketCard.tsx。
 *
 * 2026-10-01(站长 P0):只展示"有方向且历史命中率 ≥ 50%"的卡(门槛见
 * lib/market-cards.ts)。整段的外层 section 与标题由本组件渲染——没有可展示的
 * 卡时连标题一起不出现,不留一个空标题。
 */

import { useCallback, useEffect, useState } from "react";
import { clientFetch } from "@/lib/api-v1";
import type { MatchMarketCardsResponse } from "@/lib/api-v1";
import { isShowableMarketCard } from "@/lib/market-cards";
import { MarketCard } from "./MarketCard";
import styles from "./MarketCardsSection.module.css";

export function MarketCardsSection({
  matchId,
  heading,
  className,
}: {
  matchId: number;
  /** 段标题(没有可展示的卡时不渲染) */
  heading?: React.ReactNode;
  /** 外层 section 的样式(间距由调用方的页面样式决定) */
  className?: string;
}) {
  const [resp, setResp] = useState<MatchMarketCardsResponse | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    clientFetch<MatchMarketCardsResponse>(`/api/v1/matches/${matchId}/markets`)
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

  let body: React.ReactNode;
  if (error) {
    body = (
      <div className={styles.stateBox}>
        数据倾向加载失败。
        <button type="button" onClick={retry} className={styles.retryBtn}>
          重试
        </button>
      </div>
    );
  } else if (resp == null) {
    body = (
      <div className={styles.skeleton} aria-label="数据倾向加载中">
        <span className={styles.skelCard} />
        <span className={styles.skelCard} />
        <span className={styles.skelCard} />
      </div>
    );
  } else {
    const shown = resp.cards.filter(isShowableMarketCard);
    if (shown.length === 0) return null;
    body = (
      <div className={styles.grid}>
        {shown.map((card) => (
          <MarketCard key={card.market} card={card} />
        ))}
      </div>
    );
  }

  return (
    <section className={className} data-testid="market-cards-section">
      {heading}
      {body}
    </section>
  );
}
