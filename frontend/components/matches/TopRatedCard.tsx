/**
 * 总览「最高评分」卡(2026-08-25):全场评分最高的一名球员。
 *
 * 口径与命名(站长 2026-08-24 拍板):评分来自 fact_match_lineup.rating
 * (FotMob 球员评分),库里没有官方 isPlayerOfTheMatch 标志——标题用 FotMob
 * 自己的 top_rated「最高评分」措辞,不冒充官方"最佳球员"评选(CLAUDE.md
 * §2.2 不夸大数据精确度)。取数与并列裁决在后端
 * (backend/queries/match_report.py::_top_rated),前端只渲染。
 * top_rated 为 null(全场无评分)时调用方整节不渲染。
 */

import type { MatchReportResponse } from "@/lib/api-v1";
import { PlayerAvatar } from "@/components/players/PlayerAvatar";
import { RatingChip } from "@/components/matches/RatingChip";
import styles from "./TopRatedCard.module.css";

type MatchReport = Extract<MatchReportResponse, { available: true }>;
type TopRated = NonNullable<MatchReport["top_rated"]>;

export function TopRatedCard({
  topRated,
  homeName,
  awayName,
}: {
  topRated: TopRated;
  homeName: string;
  awayName: string;
}) {
  const teamName = topRated.is_home ? homeName : awayName;
  return (
    <div className={styles.card} data-testid="top-rated-card">
      <PlayerAvatar
        playerId={topRated.player_id}
        playerName={topRated.name}
        shirtNumber={topRated.shirt_number}
        size={48}
        decorative={false}
        accessibleName={`${topRated.name} 头像`}
      />
      <div className={styles.text}>
        <span className={styles.name}>
          {topRated.shirt_number ? `${topRated.shirt_number} ` : ""}
          {topRated.name}
        </span>
        <span className={styles.team}>{teamName}</span>
      </div>
      <RatingChip rating={topRated.rating} />
    </div>
  );
}
