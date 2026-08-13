/**
 * 统计 tab:球队数据对比条 → 射门图 → 球员数据表。
 * 对比行按 TEAM_STAT_LABELS 顺序;两边都缺的统计项直接跳过(不渲染假 0)。
 * 球员表窄屏砍列不缩字(DESIGN.md 移动端规则)。纯展示,无取数。
 */

import type { MatchReportResponse } from "@/lib/api-v1";
import { ShotMapChart } from "@/components/matches/ShotMapChart";
import { RatingChip } from "@/components/matches/RatingChip";
import { TEAM_STAT_LABELS } from "@/components/matches/zh";
import pageStyles from "@/app/matches/[matchId]/match-detail.module.css";
import styles from "./MatchStatsSection.module.css";

type MatchReport = Extract<MatchReportResponse, { available: true }>;
type TeamStat = MatchReport["team_stats"][number];
type PlayerStat = MatchReport["player_stats"][number];

function fmt(v: number | null | undefined, format: "pct" | "num" | "num1"): string {
  if (v == null) return "—";
  if (format === "pct") return `${Math.round(v)}%`;
  if (format === "num1") return v.toFixed(2);
  return String(Math.round(v));
}

function CompareRows({ home, away }: { home: TeamStat; away: TeamStat }) {
  const rows = TEAM_STAT_LABELS.map(({ key, label, format }) => {
    const hv = (home as Record<string, unknown>)[key] as number | null;
    const av = (away as Record<string, unknown>)[key] as number | null;
    return { key, label, format, hv, av };
  }).filter((r) => r.hv != null || r.av != null); // 两边都缺 → 来源没有这项,跳过

  if (rows.length === 0) {
    return <p className={pageStyles.emptyText}>该场比赛暂无球队统计数据。</p>;
  }
  return (
    <ul className={styles.compareList}>
      {rows.map((r) => {
        const total = (r.hv ?? 0) + (r.av ?? 0);
        const hPct = total > 0 ? ((r.hv ?? 0) / total) * 100 : 50;
        return (
          <li key={r.key} className={styles.compareRow}>
            <div className={styles.compareHeader}>
              <span className={`${styles.compareValue} num`}>{fmt(r.hv, r.format)}</span>
              <span className={styles.compareLabel}>{r.label}</span>
              <span className={`${styles.compareValue} num`}>{fmt(r.av, r.format)}</span>
            </div>
            <div className={styles.barTrack} aria-hidden>
              <span className={styles.barHome} style={{ width: `${hPct}%` }} />
              <span className={styles.barAway} style={{ width: `${100 - hPct}%` }} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

const PLAYER_COLS: { key: keyof PlayerStat; label: string; wide?: boolean }[] = [
  { key: "minutes_played", label: "分钟" },
  { key: "goals", label: "进球" },
  { key: "assists", label: "助攻" },
  { key: "expected_goals", label: "xG", wide: true },
  { key: "shots_on_target", label: "射正", wide: true },
  { key: "chances_created", label: "创造机会", wide: true },
  { key: "tackles", label: "抢断", wide: true },
];

function PlayerTable({ players, teamName }: { players: PlayerStat[]; teamName: string }) {
  if (players.length === 0) return null;
  return (
    <div className={styles.tableWrap}>
      <h3 className={styles.tableTitle}>{teamName}</h3>
      <table className={styles.playerTable}>
        <thead>
          <tr>
            <th className={styles.nameCol}>球员</th>
            <th>评分</th>
            {PLAYER_COLS.map((c) => (
              <th key={String(c.key)} className={c.wide ? styles.wideCol : undefined}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {players.map((p) => (
            <tr key={p.player_id}>
              <td className={styles.nameCol}>{p.name}</td>
              <td>
                <RatingChip rating={p.rating} />
              </td>
              {PLAYER_COLS.map((c) => {
                const v = p[c.key];
                return (
                  <td key={String(c.key)} className={`num ${c.wide ? styles.wideCol : ""}`}>
                    {typeof v === "number"
                      ? c.key === "expected_goals"
                        ? v.toFixed(2)
                        : Math.round(v)
                      : "—"}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function MatchStatsSection({
  teamStats,
  shots,
  playerStats,
  homeName,
  awayName,
}: {
  teamStats: MatchReport["team_stats"];
  shots: MatchReport["shots"];
  playerStats: MatchReport["player_stats"];
  homeName: string;
  awayName: string;
}) {
  const home = teamStats.find((t) => t.is_home);
  const away = teamStats.find((t) => !t.is_home);
  const homePlayers = playerStats.filter((p) => p.is_home);
  const awayPlayers = playerStats.filter((p) => !p.is_home);
  return (
    <section className={pageStyles.section}>
      <h2 className={pageStyles.sectionTitle}>球队数据对比</h2>
      {home && away ? (
        <CompareRows home={home} away={away} />
      ) : (
        <p className={pageStyles.emptyText}>该场比赛暂无球队统计数据。</p>
      )}

      <h2 className={pageStyles.sectionTitle}>射门图</h2>
      <ShotMapChart shots={shots} homeName={homeName} awayName={awayName} />

      <h2 className={pageStyles.sectionTitle}>球员数据</h2>
      {playerStats.length === 0 ? (
        <p className={pageStyles.emptyText}>该场比赛暂无球员统计数据。</p>
      ) : (
        <>
          <PlayerTable players={homePlayers} teamName={homeName} />
          <PlayerTable players={awayPlayers} teamName={awayName} />
        </>
      )}
    </section>
  );
}
