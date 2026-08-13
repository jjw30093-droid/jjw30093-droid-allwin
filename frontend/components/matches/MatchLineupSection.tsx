/**
 * 阵容 tab:阵型图(两队合画,主攻右/客攻左)+ 两列首发/替补名单 + 评分胶囊。
 * 评分来自后端 lineup.rating(单场评分唯一真源);替补显示上场分钟。
 * 纯展示,无取数(不写 use client,随导入方环境走)。
 */

import type { MatchReportResponse } from "@/lib/api-v1";
import { PitchFormation } from "@/components/matches/PitchFormation";
import { RatingChip } from "@/components/matches/RatingChip";
import { POSITION_ZH } from "@/components/matches/zh";
import pageStyles from "@/app/matches/[matchId]/match-detail.module.css";
import styles from "./MatchLineupSection.module.css";

type MatchReport = Extract<MatchReportResponse, { available: true }>;
type LineupTeam = MatchReport["lineups"][number];
type LineupPlayer = LineupTeam["starters"][number];

function PlayerRow({ p, bench }: { p: LineupPlayer; bench?: boolean }) {
  return (
    <li className={styles.playerRow}>
      <span className={`${styles.shirtNo} num`}>{p.shirt_number ?? "—"}</span>
      <span className={styles.pos}>
        {p.position_group ? POSITION_ZH[p.position_group] ?? p.position_group : "—"}
      </span>
      <span className={styles.name}>
        {p.name}
        {p.is_captain && <span className={styles.captain}>(队长)</span>}
        {bench && p.sub_in_time != null && (
          <span className={styles.subTime}>{p.sub_in_time}&#39; 上</span>
        )}
        {!bench && p.sub_out_time != null && (
          <span className={styles.subTime}>{p.sub_out_time}&#39; 下</span>
        )}
      </span>
      <RatingChip rating={p.rating} />
    </li>
  );
}

function TeamColumn({ team, teamName }: { team: LineupTeam; teamName: string }) {
  return (
    <div className={styles.teamCol}>
      <h3 className={styles.teamTitle}>
        {teamName}
        {team.formation && <span className={`${styles.formation} num`}>{team.formation}</span>}
      </h3>
      <h4 className={styles.groupTitle}>首发</h4>
      <ul className={styles.playerList}>
        {team.starters.map((p) => (
          <PlayerRow key={p.player_id} p={p} />
        ))}
      </ul>
      {team.bench.length > 0 && (
        <>
          <h4 className={styles.groupTitle}>替补</h4>
          <ul className={styles.playerList}>
            {team.bench.map((p) => (
              <PlayerRow key={p.player_id} p={p} bench />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

export function MatchLineupSection({
  lineups,
  homeName,
  awayName,
}: {
  lineups: MatchReport["lineups"];
  homeName: string;
  awayName: string;
}) {
  const home = lineups.find((t) => t.is_home);
  const away = lineups.find((t) => !t.is_home);
  return (
    <section className={pageStyles.section}>
      <h2 className={pageStyles.sectionTitle}>阵型与阵容</h2>
      {!home && !away ? (
        <p className={pageStyles.emptyText}>该场比赛暂无阵容数据。</p>
      ) : (
        <>
          {home && away && <PitchFormation home={home} away={away} />}
          <div className={styles.grid}>
            {home && <TeamColumn team={home} teamName={homeName} />}
            {away && <TeamColumn team={away} teamName={awayName} />}
          </div>
        </>
      )}
    </section>
  );
}
