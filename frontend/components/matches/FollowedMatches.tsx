"use client";

/**
 * 首页「我关注的比赛」:已登录读服务端关注(lib/favorites,含 localStorage
 * 一次性迁移);匿名回退浏览器本地列表(lib/followed-matches)——从不登录
 * 的老用户的关注绝不能因为这次改造凭空消失。逐场拉取公开详情渲染简短行,
 * 没有关注时整个模块不渲染(不占首屏)。取数失败的场次如实跳过,不渲染半残行。
 *
 * `variant="embedded"`(2026-08-23):供 ContinueWatching.tsx 把本组件和
 * RecentlyViewed 一起收进"继续看"区块时用——去掉自己的外层卡片样式,标题
 * 降级为 h3,通过 onVisibilityChange 把"有没有内容"回报给父级决定要不要
 * 显示整个区块。不传时保持原样(独立 section + h2),不影响既有单测。
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { clientFetch, type MatchDetailResponse } from "@/lib/api-v1";
import { loadFavorites } from "@/lib/favorites";
import { getFollowedMatchIds } from "@/lib/followed-matches";
import { LEAGUE_ZH } from "@/components/matches/zh";
import { LocalTime } from "@/components/matches/LocalTime";
import styles from "./FollowedMatches.module.css";

type Row = MatchDetailResponse["match"];

interface Props {
  variant?: "standalone" | "embedded";
  onVisibilityChange?: (visible: boolean) => void;
}

export function FollowedMatches({ variant = "standalone", onVisibilityChange }: Props = {}) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [serverBacked, setServerBacked] = useState(false);

  useEffect(() => {
    if (rows) onVisibilityChange?.(rows.length > 0);
    // onVisibilityChange 传的是 useState setter(引用稳定)或 undefined,
    // 不会因为父组件重渲染而改变身份,这里跟着 rows 变化即可。
  }, [rows, onVisibilityChange]);

  useEffect(() => {
    // 经微任务回调触发,effect 体内不同步 setState(react-hooks/set-state-in-effect)
    let cancelled = false;
    void Promise.resolve().then(async () => {
      const state = await loadFavorites().catch(() => null);
      const authed = state?.authenticated === true;
      const ids = authed ? state.ids : getFollowedMatchIds();
      if (!cancelled) setServerBacked(authed);
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

  const Heading = variant === "embedded" ? "h3" : "h2";

  return (
    <section
      className={variant === "embedded" ? styles.subsection : styles.section}
      aria-labelledby="followed-matches-title"
    >
      <Heading id="followed-matches-title" className={styles.title}>
        我关注的比赛
      </Heading>
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
      <p className={styles.hint}>
        {serverBacked
          ? "关注已保存到账号,换设备登录也能看到;在比赛详情页可关注/取消。"
          : "关注暂存在本机浏览器;登录后自动同步到账号。在比赛详情页可关注/取消。"}
      </p>
    </section>
  );
}
