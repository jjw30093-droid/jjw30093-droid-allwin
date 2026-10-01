/**
 * 首页三个入口按钮(看比赛 / 联赛数据 / 今日精选)。服务端组件(无 "use client")。
 *
 * 2026-09-26 第三批加入时上方还有一句定位文案"英超、西甲等 N 个联赛的比赛与数据"(h1);
 * 2026-10-01 站长要求删掉这句,并把推荐战绩 banner 放回首屏第一位(见 app/page.tsx)。
 */

import Link from "next/link";
import styles from "@/app/page.module.css";

export function HomeHero() {
  return (
    <section className={styles.hero} aria-label="首页入口">
      <nav className={styles.heroActions} aria-label="首页入口">
        <Link href="/matches" className={`${styles.heroBtn} ${styles.heroBtnPrimary}`}>
          看比赛
        </Link>
        <Link href="/leagues" className={styles.heroBtn}>
          联赛数据
        </Link>
        <Link href="/reco" className={styles.heroBtn}>
          今日精选
        </Link>
      </nav>
    </section>
  );
}
