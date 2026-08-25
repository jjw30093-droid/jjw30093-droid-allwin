"use client";

/**
 * 总览「重点数据」卡(2026-08-25,对齐 FotMob topStatsCard):固定 5 项
 * 左右双向条——平均控球率 / 官方统计 xG / 射门次数 / 射正 / 对方禁区内触球。
 * 项目与顺序取自 FotMob APK 卡片工厂的字节码还原(Lni7;->k,顺序即渲染
 * 顺序),不是自选的。条的颜色用双方真实球队配色(缺失/对比度不足时回退
 * 品牌 teal/navy,同 MomentumChart 的 resolveMatchColors 链路)。
 *
 * 与统计 tab 的「重点数据」分组是同一数据不同投影:这里只给 5 项 + 跳转,
 * 统计 tab 是全量分组。数值判空规则与 MatchStatsSection.CompareRowList
 * 相同:单边缺失不画条(条说"对方是 0"、数字说"未知"是自相矛盾),两边
 * 都缺整行不渲染,5 项全缺整卡不渲染。
 */

import type { MatchReportResponse } from "@/lib/api-v1";
import { useChartColors } from "@/components/charts/useChartColors";
import { resolveMatchColors, type TeamColorPair } from "@/components/charts/matchTeamColors";
import { TEAM_STAT_LABELS, formatTeamStat } from "@/components/matches/zh";
import { useMatchTabSwitch } from "./MatchTabs";
import styles from "./TopStatsCard.module.css";

type MatchReport = Extract<MatchReportResponse, { available: true }>;
type TeamStat = MatchReport["team_stats"][number];

/** FotMob topStatsCard 的 5 项(APK 字节码顺序);key 对应 MatchReportTeamStat
 * 字段,label/format 复用 zh.ts 的 TEAM_STAT_LABELS 单一真源。 */
const TOP5_KEYS = [
  "possession",
  "expected_goals",
  "total_shots",
  "shots_on_target",
  "touches_opp_box",
] as const;

const LABEL_BY_KEY = new Map(TEAM_STAT_LABELS.map((l) => [l.key, l]));

// 数值格式化收敛到 zh.ts::formatTeamStat(2026-08-25,消掉与
// MatchStatsSection 重复的第二份 fmt)。

export function TopStatsCard({
  homeStat,
  awayStat,
  homeName,
  awayName,
  homeTeamColor,
  awayTeamColor,
}: {
  homeStat: TeamStat | null;
  awayStat: TeamStat | null;
  homeName: string;
  awayName: string;
  homeTeamColor?: TeamColorPair | null;
  awayTeamColor?: TeamColorPair | null;
}) {
  const c = useChartColors();
  const switchTab = useMatchTabSwitch();
  const resolved = resolveMatchColors(homeTeamColor, awayTeamColor, {
    isDark: c.isDark,
    backgroundHex: c.surface,
    fallback: { home: c.teal, away: c.navy },
  });

  const rows = TOP5_KEYS.map((key) => {
    const meta = LABEL_BY_KEY.get(key);
    if (!meta) return null;
    const hv = homeStat ? ((homeStat as Record<string, unknown>)[key] as number | null) : null;
    const av = awayStat ? ((awayStat as Record<string, unknown>)[key] as number | null) : null;
    if (hv == null && av == null) return null; // 两边都缺 → 来源没有这项,跳过
    return { key, meta, hv, av };
  }).filter((r): r is NonNullable<typeof r> => r != null);

  if (rows.length === 0) return null;

  return (
    <div className={styles.card} data-testid="top-stats-card">
      <div className={styles.legend} aria-hidden>
        <span className={styles.legendItem}>
          <span className={styles.legendDot} style={{ background: resolved.home }} />
          {homeName}
        </span>
        <span className={styles.legendItem}>
          <span className={styles.legendDot} style={{ background: resolved.away }} />
          {awayName}
        </span>
      </div>
      <ul className={styles.list}>
        {rows.map((r) => {
          const oneSideMissing = r.hv == null || r.av == null;
          const total = (r.hv ?? 0) + (r.av ?? 0);
          const hPct = total > 0 ? ((r.hv ?? 0) / total) * 100 : 0;
          return (
            <li key={r.key} className={styles.row}>
              <div className={styles.head}>
                <span className={`${styles.value} num`}>{formatTeamStat(r.hv, r.meta)}</span>
                <span className={styles.label}>{r.meta.label}</span>
                <span className={`${styles.value} num`}>{formatTeamStat(r.av, r.meta)}</span>
              </div>
              {!oneSideMissing && (
                <div className={styles.track} aria-hidden>
                  {total > 0 && (
                    <>
                      <span
                        style={{ width: `${hPct}%`, background: resolved.home }}
                      />
                      <span
                        style={{ width: `${100 - hPct}%`, background: resolved.away }}
                      />
                    </>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {switchTab && (
        <button
          type="button"
          className={styles.moreLink}
          onClick={() => switchTab("stats")}
        >
          查看全部数据 →
        </button>
      )}
    </div>
  );
}
