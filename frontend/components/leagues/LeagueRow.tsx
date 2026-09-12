/**
 * 联赛列表的一行(2026-09-13,对齐 FotMob 安卓「联赛」tab)。
 *
 * 尺寸不是拍脑袋定的:解包 FotMob base.apk 的 res/layout/league_line.xml、
 * 用 resources.arsc 还原资源 id 后实测得到——整行 minHeight=48dp、
 * paddingStart/End=8dp、gravity=center_vertical,行内是
 * `imageView_flag`(20dp×20dp) + `textView_name`(width=0dp + weight=1 占满剩余)
 * + `button_follow`。dp 在移动端按 1:1 折算成 px,见 leagues.module.css。
 *
 * 与原版的两处刻意偏离(均经站长批准,不是遗漏):
 * 1. FotMob 那个 20dp 槽位放的是**国家旗**(组头按国家分组),我们不按国家分组、
 *    直接平铺联赛,所以这里放**联赛 logo**;
 * 2. 行尾 `button_follow`(关注星标)本轮不做——`favorites` 表与 favorites.ts
 *    都只有 match_id、零处 league,做它要新表+新端点+新 client 模块,属于账号
 *    功能而不是视觉改造。空出来的右槽改放当前赛季(这页唯一还有真实值的元数据)。
 */

import Link from "next/link";
import { LeagueBadge } from "@/components/matches/LeagueBadge";
import type { LeagueInfo } from "@/lib/api-v1";
import styles from "@/app/leagues/leagues.module.css";

export function LeagueRow({ league }: { league: LeagueInfo }) {
  // 落地页用 overview:LeagueNav 的第一个 tab 就是它,进去之后那 5 个 tab
  // (速览/排名/赛程/球队数据/球员榜)完整覆盖了原来卡片上的三个按钮。
  const href = `/league/${league.league_id}/overview`;
  const synced = league.data_status === "AVAILABLE";
  return (
    <Link href={href} className={styles.row}>
      <LeagueBadge leagueId={league.league_id} size={20} />
      <span className={styles.name}>{league.name_zh}</span>
      {/* 已同步是 17/17 的常态,给它挂个标签等于给每一行印一句废话;
          只有反面(尚未同步)才值得占位。橙色 = 等待更新(CLAUDE.md §11.2),
          **不能用红**——红在本站锁死给"真实错误或不可用"。 */}
      {!synced && <span className={styles.pending}>未同步</span>}
      {/* current_season 是从 dim_match/fact_league_table 真算出来的,为空就
          整个不渲染,不写"待同步"这种占位。 */}
      {league.current_season && (
        <span className={`${styles.season} num`}>{league.current_season}</span>
      )}
    </Link>
  );
}
