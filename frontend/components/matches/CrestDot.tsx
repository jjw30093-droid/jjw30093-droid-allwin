/**
 * 队徽当坐标点(2026-09)——`MatchProfileOverview` 的组级轴和
 * `PercentileGroupSection` 的指标行共用同一个实现,不各自维护一份。
 *
 * `TeamBadge` 没有 className/style 透传口,只能外面包一层绝对定位容器
 * 当坐标点;容器自己带主/客队色描边——描边保留"哪边是主队"的整体读法,
 * 也是 percentile-contrast.test.ts 唯一还能测合成对比度的地方(队徽本身
 * 是任意配色的位图,纯色对比度断言对它不成立)。
 */

"use client";

import { TeamBadge, type TeamBadgeSize } from "@/components/teams/TeamBadge";
import { dotLeftPct } from "./matchProfile";
import styles from "./CrestDot.module.css";

export function CrestDot({
  pct,
  crestUrl,
  teamName,
  side,
  size = 24,
}: {
  pct: number;
  crestUrl?: string | null;
  teamName: string;
  side: "home" | "away";
  size?: TeamBadgeSize;
}) {
  return (
    <span
      className={`${styles.crestDot} ${side === "home" ? styles.crestDotHome : styles.crestDotAway}`}
      style={{ left: `${dotLeftPct(pct)}%` }}
      aria-hidden
    >
      <TeamBadge teamName={teamName} crestUrl={crestUrl} size={size} />
    </span>
  );
}
