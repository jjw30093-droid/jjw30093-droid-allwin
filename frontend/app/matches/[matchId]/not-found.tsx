import type { Metadata } from "next";
import Link from "next/link";
import styles from "../../status-page.module.css";

export const metadata: Metadata = {
  title: "比赛不存在",
};

// 触发于 app/matches/[matchId]/page.tsx:matchId 不是正整数时调用 notFound()。
// 比赛真的不存在(存在但取数失败)不会走到这里,那种情况由页面自身渲染
// 客户端重新请求(见该文件顶部注释)。
export default function MatchNotFound() {
  return (
    <div className={styles.wrap}>
      <h1 className={styles.title}>找不到这场比赛</h1>
      <p className={styles.desc}>可能是链接里的编号不对，也可能这场比赛还没有收录。</p>
      <div className={styles.actions}>
        <Link href="/matches?window=7d" className={`${styles.btn} ${styles.btnPrimary}`}>
          未来七天赛程
        </Link>
        <Link href="/matches?status=finished" className={`${styles.btn} ${styles.btnGhost}`}>
          已完赛赛果
        </Link>
      </div>
    </div>
  );
}
