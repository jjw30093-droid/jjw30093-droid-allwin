/**
 * 完赛「射门」tab —— 详情页最有说服力的一屏,也是最适合搬到短视频/小红书的素材。
 *
 * 三张图按"看懂门槛"从低到高排:
 *   1. 射门威胁时间轴(双向柱)—— 不需要懂 xG,柱子高低就是"这段谁在打谁";
 *   2. xG 累积对抗曲线 —— 两条线爬升,谁该赢一眼可见;
 *   3. 射门落点图 —— 空间本身即解释,可按结果/部位/时段筛选。
 *
 * 每张图都自带文字摘要(宪法 §11.2),摘要里把 xG 翻译成人话,不假设读者
 * 知道术语。
 */

import type { MatchReportResponse } from "@/lib/api-v1";
import type { TeamColorPair } from "@/components/charts/matchTeamColors";
import { ThreatTimeline } from "@/components/matches/ThreatTimeline";
import { MomentumChart } from "@/components/matches/MomentumChart";
import { XgRaceChart } from "@/components/matches/XgRaceChart";
import { ShotMapChart } from "@/components/matches/ShotMapChart";
import styles from "@/app/matches/[matchId]/match-detail.module.css";

type MatchReport = Extract<MatchReportResponse, { available: true }>;

export function MatchShotsSection({
  shots,
  momentum,
  homeName,
  awayName,
  homeTeamColor,
  awayTeamColor,
  homeScore,
  awayScore,
}: {
  shots: MatchReport["shots"];
  momentum: MatchReport["momentum"];
  homeName: string;
  awayName: string;
  /** 2026-08-24:真实球队配色,原样转发给下面 4 张图表,组件内部各自回退。 */
  homeTeamColor?: TeamColorPair | null;
  awayTeamColor?: TeamColorPair | null;
  homeScore?: number | null;
  awayScore?: number | null;
}) {
  const inPlay = shots.filter((s) => s.period !== "PenaltyShootout");
  if (inPlay.length === 0) {
    return <p className={styles.emptyText}>本场暂无射门数据。</p>;
  }

  return (
    <>
      {momentum.length > 0 && (
        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>势头图</h2>
          <MomentumChart
            momentum={momentum}
            homeName={homeName}
            awayName={awayName}
            homeTeamColor={homeTeamColor}
            awayTeamColor={awayTeamColor}
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

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>射门落点</h2>
        <ShotMapChart
          shots={shots}
          homeName={homeName}
          awayName={awayName}
          homeTeamColor={homeTeamColor}
          awayTeamColor={awayTeamColor}
        />
      </section>
    </>
  );
}
