import styles from "@/components/league/MemberLeagueSection.module.css";

/**
 * 比赛详情页(动态路由,server component 逐段请求数据)的 Next.js App Router
 * loading boundary。没有这个文件时,<Link> 在 touchstart 就发起的预取
 * (Next.js 16 segment cache)会让触屏点击在预取结果返回前看起来"点了没
 * 反应"——桌面鼠标点击/程序化 .click() 不经过 touchstart,不受影响,这也是
 * 生产反馈里"点击比赛跳转不到详情页"只在触屏上出现的原因。这里给的是诚实的
 * 加载态,不是让请求变快;沿用 MemberMatchDetail 已在用的同一套骨架
 * (MemberLeagueSection.module.css 的 .skeleton/.skelLine),视觉与既有加载态一致。
 */
export default function Loading() {
  return (
    <div className={styles.skeleton} aria-label="比赛详情加载中">
      <span className={styles.skelLine} />
      <span className={styles.skelLine} />
      <span className={styles.skelLine} />
    </div>
  );
}
