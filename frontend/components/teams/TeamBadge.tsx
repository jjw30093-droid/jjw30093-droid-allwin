"use client";

import Image from "next/image";
import { useState } from "react";
import styles from "./TeamBadge.module.css";

export type TeamBadgeSize = 24 | 28 | 32 | 36 | 40 | 48 | 56;

const SIZE_CLASS: Record<TeamBadgeSize, string> = {
  24: styles.size24,
  28: styles.size28,
  32: styles.size32,
  36: styles.size36,
  40: styles.size40,
  48: styles.size48,
  56: styles.size56,
};

export function teamInitials(teamName: string): string {
  const normalized = teamName.trim().replace(/\s+/g, " ");
  if (!normalized) return "队";
  const words = normalized.split(" ");
  if (words.length > 1 && words.every((word) => /^[A-Za-z]/.test(word))) {
    return words
      .slice(0, 2)
      .map((word) => word[0])
      .join("")
      .toUpperCase();
  }
  return Array.from(normalized).slice(0, 2).join("").toUpperCase();
}

export function TeamBadge({
  teamName,
  crestUrl,
  size = 32,
  decorative = true,
  accessibleName,
  eager = false,
}: {
  teamName: string;
  crestUrl?: string | null;
  size?: TeamBadgeSize;
  decorative?: boolean;
  accessibleName?: string;
  eager?: boolean;
}) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const showImage = Boolean(crestUrl && failedUrl !== crestUrl);
  const label = accessibleName ?? `${teamName}队徽`;
  const className = `${styles.badge} ${SIZE_CLASS[size]}`;

  if (!showImage) {
    return (
      <span
        className={`${className} ${styles.fallback}`}
        aria-hidden={decorative ? true : undefined}
        role={decorative ? undefined : "img"}
        aria-label={decorative ? undefined : label}
        data-testid="team-badge-fallback"
      >
        {teamInitials(teamName)}
      </span>
    );
  }

  return (
    <span className={className} data-testid="team-badge-image">
      <Image
        className={styles.image}
        src={crestUrl!}
        width={size}
        height={size}
        alt={decorative ? "" : label}
        loading={eager ? "eager" : "lazy"}
        unoptimized
        onError={() => setFailedUrl(crestUrl!)}
      />
    </span>
  );
}
