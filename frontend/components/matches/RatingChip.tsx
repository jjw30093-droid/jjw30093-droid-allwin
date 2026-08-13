/**
 * 球员单场评分胶囊(DESIGN.md §2.6:阈值 ≥7.5 elite / 7.0-7.49 good /
 * 6.5-6.99 mid / <6.5 low;色值用 globals.css 现有 --rate-* token,不新开色相)。
 * elite 档底色在浅色主题是深青、深色主题是亮青,前景反转方向相反——
 * 统一用 var(--bg) 做前景,两种主题下都天然高对比,零新增 token。
 */

import styles from "./RatingChip.module.css";

export function RatingChip({ rating }: { rating: number | null | undefined }) {
  if (rating == null) return null;
  const tier =
    rating >= 7.5 ? styles.elite : rating >= 7.0 ? styles.good : rating >= 6.5 ? styles.mid : styles.low;
  return <span className={`${styles.chip} ${tier} num`}>{rating.toFixed(1)}</span>;
}
