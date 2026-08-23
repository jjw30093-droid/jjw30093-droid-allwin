import type { Metadata } from "next";
import Link from "next/link";
import styles from "./status-page.module.css";

export const metadata: Metadata = {
  title: "页面不存在",
};

export default function NotFound() {
  return (
    <div className={styles.wrap}>
      <h1 className={styles.title}>这个页面不在了</h1>
      <p className={styles.desc}>链接可能改过，或者从来就没有这个地址。</p>
      <div className={styles.actions}>
        <Link href="/" className={`${styles.btn} ${styles.btnPrimary}`}>
          回首页
        </Link>
        <Link href="/matches?window=today" className={`${styles.btn} ${styles.btnGhost}`}>
          看今天的比赛
        </Link>
        <Link href="/leagues" className={`${styles.btn} ${styles.btnGhost}`}>
          全部联赛
        </Link>
      </div>
    </div>
  );
}
