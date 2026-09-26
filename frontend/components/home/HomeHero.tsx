/**
 * 首页首屏第一块(2026-09-26 第三批):一句话定位 + 三个入口按钮。
 *
 * 服务端组件(无 "use client")。联赛数从联赛配置计算(LEAGUE_COUNT),新增联赛
 * 自动跟着变,不写死数字。战绩条不再占首屏第一位,挪进「今日精选」卡内部。
 */

import Link from "next/link";
import { LEAGUE_COUNT } from "@/components/matches/zh";
import styles from "@/app/page.module.css";

export const HERO_TAGLINE = `英超、西甲等 ${LEAGUE_COUNT} 个联赛的比赛与数据`;

export function HomeHero() {
  return (
    <section className={styles.hero} aria-labelledby="home-hero-title">
      <h1 id="home-hero-title" className={styles.heroTitle}>
        {HERO_TAGLINE}
      </h1>
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
