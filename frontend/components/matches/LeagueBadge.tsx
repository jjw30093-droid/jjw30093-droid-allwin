"use client";

/**
 * 联赛队徽——静态资源(frontend/public/brand/leagues/{league_id}.png),
 * 不像球队队徽走 /api/v1/media 那条按需采集+校验管线:联赛是 LEAGUE_META
 * 里固定的 17 个,不随数据采集动态增减,直接自托管一份小图更省事
 * (CLAUDE.md §11.2 字体图片自托管,不依赖外链)。
 *
 * 找不到对应文件(未来新增联赛还没补图)时静默不渲染,不占位、不用球队徽的
 * 缩写兜底样式——联赛名文字本来就在旁边,没有图标不影响可读性。
 */

import Image from "next/image";
import { useState } from "react";
import styles from "./LeagueBadge.module.css";

export type LeagueBadgeSize = 14 | 16 | 20;

const SIZE_CLASS: Record<LeagueBadgeSize, string> = {
  14: styles.size14,
  16: styles.size16,
  20: styles.size20,
};

export function LeagueBadge({
  leagueId,
  size = 16,
}: {
  leagueId: number;
  size?: LeagueBadgeSize;
}) {
  const [failed, setFailed] = useState(false);
  if (failed) return null;
  return (
    <Image
      className={`${styles.badge} ${SIZE_CLASS[size]}`}
      src={`/brand/leagues/${leagueId}.png`}
      width={size}
      height={size}
      alt=""
      unoptimized
      onError={() => setFailed(true)}
    />
  );
}
