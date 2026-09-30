import type { ReactNode } from "react";
import styles from "./simulator.module.css";

/** 折叠区:原生 <details>(与站内 LeaderboardCard / 比赛页"数据来源与说明"同一做法,不引入 JS accordion),
 *  外观与本页卡片一致;默认收起。 */
export function Fold({ title, hint, children, testId }: { title: string; hint?: string; children: ReactNode; testId?: string }) {
  return (
    <details className={`${styles.card} ${styles.fold}`} data-testid={testId}>
      <summary className={styles.foldSummary}>
        <span className={styles.foldHead}>
          <span className={styles.foldTitle}>{title}</span>
          {hint ? <span className={styles.foldHint}>{hint}</span> : null}
        </span>
        <span className={styles.foldChevron} aria-hidden="true" />
      </summary>
      <div className={styles.foldBody}>{children}</div>
    </details>
  );
}
