"use client";

/**
 * 首页「最近浏览」:读取本机 localStorage 浏览记录,逐场拉公开详情渲染
 * 简短行。已在「我关注的比赛」出现的场次不重复展示;没有记录时整个模块
 * 不渲染。样式与 FollowedMatches 共用同一 CSS 模块(同一视觉,不复制样式)。
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { clientFetch, type MatchDetailResponse } from "@/lib/api-v1";
import { getFollowedMatchIds } from "@/lib/followed-matches";
import { getRecentlyViewedIds } from "@/lib/recently-viewed";
import { LEAGUE_ZH } from "@/components/matches/zh";
import { LocalTime } from "@/components/matches/LocalTime";
import styles from "./FollowedMatches.module.css";

type Row = MatchDetailResponse["match"];

export function RecentlyViewed() {
  const [rows, setRows] = useState<Row[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    void Promise.resolve().then(async () => {
      const followed = new Set(getFollowedMatchIds());
      const ids = getRecentlyViewedIds().filter((id) => !followed.has(id));
      if (ids.length === 0) {
        if (!cancelled) setRows([]);
        return;
      }
      const list = await Promise.all(
        ids.slice(0, 5).map((id) =>
          clientFetch<MatchDetailResponse>(`/api/v1/matches/${id}`)
            .then((d) => d.match)
            .catch(() => null),
        ),
      );
      if (!cancelled) setRows(list.filter((m): m is Row => m != null));
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!rows || rows.length === 0) return null;

  return (
    <section className={styles.section} aria-labelledby="recently-viewed-title">
      <h2 id="recently-viewed-title" className={styles.title}>
        最近浏览
      </h2>
      <ul className={styles.list}>
        {rows.map((m) => (
          <li key={m.match_id}>
            <Link href={`/matches/${m.match_id}`} className={styles.row}>
              <span className={styles.league}>
                {LEAGUE_ZH[m.league_id] ?? `联赛 ${m.league_id}`}
              </span>
              <span className={styles.teams}>
                {m.home.name} vs {m.away.name}
              </span>
              {/* 已完赛显示比分,不显示开球时间——这两块保存的是 match_id,
                  比赛踢完后仍留在列表里,继续显示"开球时间"会让用户以为比赛
                  还没开始(CLAUDE.md §2.2)。判据与 MatchRow.tsx 完全一致:
                  status==="Finish" 且两个 score 都非空才算有比分,缺分时如实
                  退回时间,不编造。 */}
              {m.status === "Finish" && m.home_score != null && m.away_score != null ? (
                <span className={`${styles.time} num`}>
                  {m.home_score} - {m.away_score}
                </span>
              ) : (
                <span className={styles.time}>
                  {m.kickoff_at_utc ? (
                    <LocalTime iso={m.kickoff_at_utc} fallback={m.date_utc} />
                  ) : (
                    m.date_utc
                  )}
                </span>
              )}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
