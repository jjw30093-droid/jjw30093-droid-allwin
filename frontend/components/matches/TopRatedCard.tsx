/**
 * 总览「最佳球员/最高评分」卡(2026-08-25 修订)。
 *
 * 口径与命名:is_official=True 表示命中 FotMob 官方 playerOfTheMatch 标志
 * (2026-08-25 更正:该标志一直在库里,覆盖 13045/13050 场,此前误判"没有"
 * 而只做了评分口径),标题用「最佳球员」;is_official=False 是官方标志缺失
 * 时退回的全场最高评分,标题用「最高评分」,不冒充官方评选(CLAUDE.md §2.2)。
 * 判定与并列裁决都在后端(backend/queries/match_report.py::_top_rated),
 * 前端只按 is_official 选标题,不自算口径。section 标题由调用方
 * (MatchDetailBody)用同一个 topRatedTitle() 取,两处不会漂移。
 * top_rated 为 null(全场无评分)时调用方整节不渲染。
 */

import type { MatchReportResponse } from "@/lib/api-v1";
import { PlayerAvatar } from "@/components/players/PlayerAvatar";
import { RatingChip } from "@/components/matches/RatingChip";
import styles from "./TopRatedCard.module.css";

type MatchReport = Extract<MatchReportResponse, { available: true }>;
type TopRated = NonNullable<MatchReport["top_rated"]>;

/** section 标题(卡片与 MatchDetailBody 的 SectionTitle 共用,单一出口)。 */
export function topRatedTitle(topRated: TopRated): string {
  return topRated.is_official ? "最佳球员" : "最高评分";
}

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
