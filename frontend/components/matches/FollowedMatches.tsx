"use client";

/**
 * 首页「我关注的比赛」:读取浏览器本地关注列表(lib/followed-matches),
 * 逐场拉取公开详情渲染简短行。没有关注时整个模块不渲染(不占首屏)。
 * 匿名无权限的联赛(401/403)如实跳过,不渲染半残行。
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { clientFetch, type MatchDetailResponse } from "@/lib/api-v1";
import { getFollowedMatchIds } from "@/lib/followed-matches";
import { LEAGUE_ZH } from "@/components/matches/zh";
import { LocalTime } from "@/components/matches/LocalTime";
import styles from "./FollowedMatches.module.css";

type Row = MatchDetailResponse["match"];

export function FollowedMatches() {
  const [rows, setRows] = useState<Row[] | null>(null);

  useEffect(() => {
    // 经微任务回调触发,effect 体内不同步 setState(react-hooks/set-state-in-effect)
    let cancelled = false;
    void Promise.resolve().then(async () => {
      const ids = getFollowedMatchIds();
      if (ids.length === 0) {
        if (!cancelled) setRows([]);
        return;
      }
      const list = await Promise.all(
        ids.slice(0, 8).map((id) =>
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
    <section className={styles.section} aria-labelledby="followed-matches-title">
      <h2 id="followed-matches-title" className={styles.title}>
        我关注的比赛
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
              <span className={styles.time}>
                {m.kickoff_at_utc ? (
                  <LocalTime iso={m.kickoff_at_utc} fallback={m.date_utc} />
                ) : (
                  m.date_utc
                )}
              </span>
            </Link>
          </li>
        ))}
      </ul>
      <p className={styles.hint}>关注保存在本机浏览器;在比赛详情页可关注/取消。</p>
    </section>
  );
}
