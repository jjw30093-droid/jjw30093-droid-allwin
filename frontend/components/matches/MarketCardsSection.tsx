"use client";

/**
 * 市场卡列表(比赛详情页新首要区块,赛前/完赛均可用)。
 * 结构:结论区常驻 + 驱动因子折叠,见 MarketCard.tsx。
 */

import { useCallback, useEffect, useState } from "react";
import { clientFetch } from "@/lib/api-v1";
import type { MatchMarketCardsResponse } from "@/lib/api-v1";
import { MarketCard } from "./MarketCard";
import styles from "./MarketCardsSection.module.css";

export function MarketCardsSection({ matchId }: { matchId: number }) {
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

  if (error) {
    return (
      <div className={styles.stateBox}>
        数据倾向加载失败。
        <button type="button" onClick={retry} className={styles.retryBtn}>
          重试
        </button>
      </div>
    );
  }
  if (resp == null) {
    return (
      <div className={styles.skeleton} aria-label="数据倾向加载中">
        <span className={styles.skelCard} />
        <span className={styles.skelCard} />
        <span className={styles.skelCard} />
      </div>
    );
  }
  if (resp.cards.length === 0) return null;

  return (
    <div className={styles.grid}>
      {resp.cards.map((card) => (
        <MarketCard key={card.market} card={card} />
      ))}
    </div>
  );
}
