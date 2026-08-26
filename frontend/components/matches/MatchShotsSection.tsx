/**
 * 完赛「射门」tab(2026-08-25 对齐 FotMob 重排,站长要求):
 *   1. 射门落点图放最顶部 —— 空间本身即解释,点击射门点看单次详情;
 *   2. 射门威胁时间轴(双向柱)—— 不需要懂 xG,柱子高低就是"这段谁在打谁";
 *   3. xG 累积对抗曲线 —— 两条线爬升,谁该赢一眼可见。
 * 势头图整块挪去「总览」tab 顶部(FotMob 的 moveMomentumAndStatsToTop
 * 已完赛行为),不在两个 tab 重复。
 *
 * 每张图都自带文字摘要(宪法 §11.2),摘要里把 xG 翻译成人话,不假设读者
 * 知道术语。
 */

import type { MatchReportResponse } from "@/lib/api-v1";
import type { TeamColorPair } from "@/components/charts/matchTeamColors";
import { ThreatTimeline } from "@/components/matches/ThreatTimeline";
import { XgRaceChart } from "@/components/matches/XgRaceChart";
import { ShotMapChart } from "@/components/matches/ShotMapChart";
import { AttackingZonesChart } from "@/components/matches/AttackingZonesChart";
import {
  zoneSplitFrom,
  type AttackingZoneSplit,
} from "@/components/matches/attackingZones";
import styles from "@/app/matches/[matchId]/match-detail.module.css";

type MatchReport = Extract<MatchReportResponse, { available: true }>;
type TeamStat = MatchReport["team_stats"][number];

/** 某侧某时段的进攻区域三分区(投影自 MatchReportTeamStat 的
 * attacking_zone_* 字段;行缺失或任一字段缺失 → null,不补 0)。 */
function zoneSplitOf(rows: TeamStat[], isHome: boolean, period: string): AttackingZoneSplit | null {
  const row = rows.find((t) => t.is_home === isHome && t.period === period);
  if (!row) return null;
  return zoneSplitFrom(row.attacking_zone_left, row.attacking_zone_center, row.attacking_zone_right);
}

export function MatchShotsSection({
  shots,
  lineups,
  teamStats = [],
  teamStatsByHalf = [],
  homeName,
  awayName,
  homeTeamColor,
  awayTeamColor,
  homeCrestUrl,
  awayCrestUrl,
  homeScore,
  awayScore,
}: {
  shots: MatchReport["shots"];
  /** 2026-08-24:射门详情面板要用——展开成球衣号映射表,查得到才显示。 */
  lineups: MatchReport["lineups"];
  /** 2026-08-25:进攻区域图的数据源(attacking_zone_* 已投影进球队统计行,
   * 不另设 DTO 字段);缺失时该小节整个不渲染。 */
  teamStats?: MatchReport["team_stats"];
  teamStatsByHalf?: MatchReport["team_stats_by_half"];
  homeName: string;
  awayName: string;
  /** 2026-08-24:真实球队配色,原样转发给下面各图表,组件内部各自回退。 */
  homeTeamColor?: TeamColorPair | null;
  awayTeamColor?: TeamColorPair | null;
  homeCrestUrl?: string | null;
  awayCrestUrl?: string | null;
  homeScore?: number | null;
  awayScore?: number | null;
}) {
  const inPlay = shots.filter((s) => s.period !== "PenaltyShootout");
  if (inPlay.length === 0) {
    return <p className={styles.emptyText}>本场暂无射门数据。</p>;
  }

  // 首发 + 替补都要收——漏了替补会导致替补登场球员的射门在详情面板里
  // 查不到球衣号。
  const shirtNumberByPlayerId = Object.fromEntries(
    lineups
      .flatMap((t) => [...t.starters, ...t.bench])
      .filter((p) => p.shirt_number != null)
      .map((p) => [p.player_id, p.shirt_number as string]),
  );

  // 进攻区域:全场取 team_stats(period='All'),半场取 team_stats_by_half;
  // AttackingZonesChart 自己判空(两侧全缺整个组件不渲染)。
  const zonesHome = zoneSplitOf(teamStats, true, "All");
  const zonesAway = zoneSplitOf(teamStats, false, "All");
  const zonesByPeriod = {
    FirstHalf: {
      home: zoneSplitOf(teamStatsByHalf, true, "FirstHalf"),
      away: zoneSplitOf(teamStatsByHalf, false, "FirstHalf"),
    },
    SecondHalf: {
      home: zoneSplitOf(teamStatsByHalf, true, "SecondHalf"),
      away: zoneSplitOf(teamStatsByHalf, false, "SecondHalf"),
    },
  };
  const hasZones =
    zonesHome != null ||
    zonesAway != null ||
    Object.values(zonesByPeriod).some((p) => p.home != null || p.away != null);

  return (
    <>
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>射门落点</h2>
        <ShotMapChart
          shots={shots}
          homeName={homeName}
          awayName={awayName}
          homeTeamColor={homeTeamColor}
          awayTeamColor={awayTeamColor}
          homeCrestUrl={homeCrestUrl}
          awayCrestUrl={awayCrestUrl}
          shirtNumberByPlayerId={shirtNumberByPlayerId}
        />
      </section>

      {hasZones && (
        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>进攻区域</h2>
          <AttackingZonesChart
            home={zonesHome}
            away={zonesAway}
            homeName={homeName}
            awayName={awayName}
            homeTeamColor={homeTeamColor}
            awayTeamColor={awayTeamColor}
            byPeriod={zonesByPeriod}
          />
        </section>
      )}

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>射门威胁时间轴</h2>
        <ThreatTimeline
          shots={shots}
          homeName={homeName}
          awayName={awayName}
          homeTeamColor={homeTeamColor}
          awayTeamColor={awayTeamColor}
        />
      </section>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>xG 累积对抗</h2>
        <XgRaceChart
          shots={shots}
          homeName={homeName}
          awayName={awayName}
          homeTeamColor={homeTeamColor}
          awayTeamColor={awayTeamColor}
          homeScore={homeScore}
          awayScore={awayScore}
        />
      </section>
    </>
  );
}
