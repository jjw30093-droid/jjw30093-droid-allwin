/**
 * 单场阵型图:两队同画在一块球场上(主队攻向右,客队镜像攻向左)。
 * 纯 CSS 绝对定位,零 ECharts、零新依赖——来源 extra_json.horizontalLayout
 * 已给出 0..1 归一化站位(x=0.1 门将端),定位难题上游已解。
 *
 * 坐标映射(合并单场视图):
 *   主队:left = pitch_x/2          (占左半场,门将贴左边线)
 *   客队:left = 1 - pitch_x/2      (镜像占右半场,门将贴右边线)
 *   客队 top 同步镜像(1 - pitch_y),保持阵型左右对称的观感。
 * 任一队全员缺 pitch 坐标时该队不画点(调用方降级为纯列表,不猜站位)。
 */

import { PlayerAvatar } from "@/components/players/PlayerAvatar";
import type { MatchReportResponse } from "@/lib/api-v1";
import { FootballPitchBackground } from "./FootballPitchBackground";
import styles from "./PitchFormation.module.css";

type MatchReport = Extract<MatchReportResponse, { available: true }>;
type LineupTeam = MatchReport["lineups"][number];

function dots(team: LineupTeam) {
  return team.starters
    .filter((p) => p.pitch_x != null && p.pitch_y != null)
    .map((p) => {
      const x = team.is_home ? p.pitch_x! / 2 : 1 - p.pitch_x! / 2;
      const y = team.is_home ? p.pitch_y! : 1 - p.pitch_y!;
      return { ...p, left: x * 100, top: y * 100 };
    });
}

export function PitchFormation({ home, away }: { home: LineupTeam; away: LineupTeam }) {
  const homeDots = dots(home);
  const awayDots = dots(away);
  if (homeDots.length === 0 && awayDots.length === 0) return null;
  return (
    <div
      className={styles.pitch}
      role="img"
      aria-label={`阵型图:主队 ${home.formation ?? "未知阵型"}(攻向右),客队 ${
        away.formation ?? "未知阵型"
      }(攻向左)`}
    >
      <FootballPitchBackground />
      {homeDots.map((p) => (
        <div
          key={p.player_id}
          className={`${styles.player} ${styles.playerHome}`}
          style={{ left: `${p.left}%`, top: `${p.top}%` }}
        >
          <span className={styles.avatarRing}>
            <PlayerAvatar playerId={p.player_id} playerName={p.name} shirtNumber={p.shirt_number} size={40} />
          </span>
          <span className={styles.playerName}>
            {p.shirt_number ? `${p.shirt_number} ` : ""}
            {p.name}
          </span>
        </div>
      ))}
      {awayDots.map((p) => (
        <div
          key={p.player_id}
          className={`${styles.player} ${styles.playerAway}`}
          style={{ left: `${p.left}%`, top: `${p.top}%` }}
        >
          <span className={styles.avatarRing}>
            <PlayerAvatar playerId={p.player_id} playerName={p.name} shirtNumber={p.shirt_number} size={40} />
          </span>
          <span className={styles.playerName}>
            {p.shirt_number ? `${p.shirt_number} ` : ""}
            {p.name}
          </span>
        </div>
      ))}
    </div>
  );
}
