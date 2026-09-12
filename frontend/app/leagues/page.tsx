/**
 * 联赛列表页(2026-09-13 按 FotMob 安卓「联赛」tab 重做)。
 *
 * 改造前是一个联赛一张大卡(中文名 + badge + 英文名 + 当前赛季 + 最近更新 +
 * 三个按钮),手机上一屏只看得到 4 个联赛;FotMob 是 48dp 单行、一屏 ~15 个。
 * 现在改成单行列表,细节见 components/leagues/LeagueRow.tsx 的取证说明。
 *
 * **顺序直接用后端给的**,这里不再排序。原来这里排过一次:
 *   priority(AVAILABLE ? 0 : 1) || name_zh.localeCompare("zh-CN")
 * 而 17 个联赛实测全是 AVAILABLE ⇒ priority 恒为 0 ⇒ 整个排序塌缩成拼音序,
 * 把澳超/巴甲排到英超前面。热度序现在是后端 LEAGUE_DISPLAY_ORDER 的职责
 * (单一真源,前端不再维护第二套排序规则)。
 *
 * 保持纯 Server Component:全页没有任何交互状态,LeagueBadge 虽然是
 * "use client",但从 RSC **渲染**一个 Client Component 是合法的——§11.4 禁的是
 * 从 client 文件里 import 非组件符号。
 */

import type { Metadata } from "next";
import Link from "next/link";
import { LeagueRow } from "@/components/leagues/LeagueRow";
import { serverGet, type LeagueInfo } from "@/lib/api-v1";
import styles from "./leagues.module.css";

export const metadata: Metadata = {
  title: "联赛数据",
  description: "浏览各联赛的排名、赛程、球队与球员数据。",
};

export default async function LeaguesPage() {
  let leagues: LeagueInfo[];
  try {
    leagues = await serverGet<LeagueInfo[]>("/api/v1/leagues", {
      revalidate: 60,
    });
  } catch {
    return (
      <main className={styles.page}>
        <h1>联赛数据</h1>
        <div className={styles.errorBox}>
          数据暂时无法加载，请稍后刷新重试。
        </div>
      </main>
    );
  }

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div>
          <h1>联赛数据</h1>
          <p>点任意联赛进入它的排名、赛程、球队与球员数据。</p>
        </div>
        <Link href="/matches?status=upcoming&window=7d" className={styles.matchLink}>
          查看未来七天比赛 →
        </Link>
      </header>

      <div className={styles.list}>
        {leagues.map((league) => (
          <LeagueRow key={league.league_id} league={league} />
        ))}
      </div>
    </main>
  );
}
