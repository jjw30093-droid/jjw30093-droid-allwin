"use client";

import Link from "next/link";
import styles from "./status-page.module.css";

// 路由树内的客户端渲染异常兜底(错误边界,必须是 Client Component)。
// 不渲染 error.message / error.digest 给用户——生产环境的 message 本来就
// 是脱敏后的通用文案,堆栈信息只该进服务端日志,不该出现在页面上。
export default function ErrorPage({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className={styles.wrap}>
      <h1 className={styles.title}>页面出错了</h1>
      <p className={styles.desc}>刷新一下多半就好。一直不行的话，稍后再来。</p>
      <div className={styles.actions}>
        <button
          type="button"
          onClick={() => reset()}
          className={`${styles.btn} ${styles.btnPrimary}`}
        >
          重试
        </button>
        <Link href="/" className={`${styles.btn} ${styles.btnGhost}`}>
          回首页
        </Link>
      </div>
    </div>
  );
}
