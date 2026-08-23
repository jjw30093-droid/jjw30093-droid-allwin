"use client";

/**
 * 盘口参考卡片列表(比赛详情页首要区块之一,赛前/完赛均可用)。
 * 结构:结论区常驻 + 驱动因子折叠,见 MarketCard.tsx。
 *
 * 2026-08-23 单栏重排:标题从父组件(MatchDetailBody)挪进本组件——此前
 * 标题在父组件渲染、"有没有卡片"的判空在这里,两处分离导致没有卡片数据
 * 的场次会露出一个孤零零的空标题。现在标题和内容判空绑在一起:确认没有
 * 卡片时整个区块(含标题)一起不渲染;加载中/加载失败仍展示标题,因为
 * 那是临时状态,不是"这场比赛没有这个板块"。
 *
 * grep 过 frontend/app/studio/,Creator Studio 没有复用这个组件
 * (studio/types.ts 里的"数据可视化"是六段式竖屏场景的独立标签,与本组件
 * 无关),因此这里不需要 showTitle 之类的开关。
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

  // 确认没有卡片数据(接口正常返回、cards 为空数组)时,标题和内容一起
  // 消失——不是加载中或加载失败,是这场比赛确实没有这个板块。
  if (resp != null && resp.cards.length === 0) return null;

  return (
    <section className={styles.section}>
      <h2 className={styles.sectionTitle}>
        <span className={styles.sectionBar} aria-hidden />
        盘口参考
      </h2>
      <p className={styles.sectionNote}>
        按两队近期数据，在历史同类局面里查这条线过了多少次。
      </p>
      {error ? (
        <div className={styles.stateBox}>
          盘口参考加载失败。
          <button type="button" onClick={retry} className={styles.retryBtn}>
            重试
          </button>
        </div>
      ) : resp == null ? (
        <div className={styles.skeleton} aria-label="盘口参考加载中">
          <span className={styles.skelCard} />
          <span className={styles.skelCard} />
          <span className={styles.skelCard} />
        </div>
      ) : (
        <div className={styles.grid}>
          {resp.cards.map((card) => (
            <MarketCard key={card.market} card={card} />
          ))}
        </div>
      )}
    </section>
  );
}
