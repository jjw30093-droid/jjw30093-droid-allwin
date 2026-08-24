"use client";

/**
 * 球员头像(2026-08-24)——frontend/components/teams/TeamBadge.tsx 的同构
 * 复刻(同一个 img 优先/onError 回退状态机),用于射门详情面板、预测阵容
 * 球场图/替补/教练行、真实首发阵型图/球员数据表。
 *
 * 2026-08-24 经站长明确批准的一次性例外:球员头像直接热链 FotMob 图片
 * CDN,不新建自托管代理/缓存层——这是对 CLAUDE.md §11.2"图片自托管、
 * 不依赖外链"通用规则的一次性例外,只用于这一个头像场景,不代表该规则
 * 废止。绝不能出现在 Studio 导出路径(html-to-image 截图,跨源 <img> 会
 * 污染 canvas,见 frontend/next.config.ts 队徽走同源 rewrite 的同一顾虑)。
 */

import Image from "next/image";
import { useState } from "react";
import styles from "./PlayerAvatar.module.css";

export type PlayerAvatarSize = 24 | 28 | 32 | 36 | 40 | 48 | 56;

const SIZE_CLASS: Record<PlayerAvatarSize, string> = {
  24: styles.size24,
  28: styles.size28,
  32: styles.size32,
  36: styles.size36,
  40: styles.size40,
  48: styles.size48,
  56: styles.size56,
};

const PLAYER_AVATAR_BASE = "https://images.fotmob.com/image_resources/playerimages";

/** 文字兜底:球衣号优先,否则姓名首字符(单字符,不是 TeamBadge.teamInitials
 * 的双字符缩写——球员场景既有惯例是"球衣号+姓名"分开展示,不是缩写)。 */
function fallbackText(playerName: string, shirtNumber?: string | null): string {
  if (shirtNumber) return shirtNumber;
  const trimmed = playerName.trim();
  return trimmed ? Array.from(trimmed)[0] : "?";
}

export function PlayerAvatar({
  playerId,
  playerName,
  shirtNumber,
  size = 32,
  decorative = true,
  accessibleName,
  eager = false,
}: {
  playerId: string | number;
  playerName: string;
  shirtNumber?: string | null;
  size?: PlayerAvatarSize;
  decorative?: boolean;
  accessibleName?: string;
  eager?: boolean;
}) {
  const url = `${PLAYER_AVATAR_BASE}/${playerId}.png`;
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const showImage = failedUrl !== url;
  const label = accessibleName ?? `${playerName}头像`;
  const className = `${styles.avatar} ${SIZE_CLASS[size]}`;

  if (!showImage) {
    return (
      <span
        className={`${className} ${styles.fallback}`}
        aria-hidden={decorative ? true : undefined}
        role={decorative ? undefined : "img"}
        aria-label={decorative ? undefined : label}
        data-testid="player-avatar-fallback"
      >
        {fallbackText(playerName, shirtNumber)}
      </span>
    );
  }

  return (
    <span className={className} data-testid="player-avatar-image">
      <Image
        className={styles.image}
        src={url}
        width={size}
        height={size}
        alt={decorative ? "" : label}
        loading={eager ? "eager" : "lazy"}
        unoptimized
        onError={() => setFailedUrl(url)}
      />
    </span>
  );
}
