"use client";

/**
 * Studio 门禁:仅 analyst/admin 可进(后端是权限真源,403 一律整页提示;
 * 这里只做体验层,受限数据本身不会出现在匿名 HTML/RSC payload 里)。
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { getMe, type MeResponse } from "@/lib/api-v1";
import styles from "./StudioGate.module.css";

type GateState =
  | { phase: "loading" }
  | { phase: "anonymous" }
  | { phase: "forbidden"; role: string }
  | { phase: "error"; message: string }
  | { phase: "ok"; me: MeResponse };

export function StudioGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<GateState>({ phase: "loading" });

  useEffect(() => {
    let cancelled = false;
    getMe()
      .then((me) => {
        if (cancelled) return;
        if (!me.authenticated || !me.user) {
          setState({ phase: "anonymous" });
        } else if (me.user.role === "analyst" || me.user.role === "admin") {
          setState({ phase: "ok", me });
        } else {
          setState({ phase: "forbidden", role: me.user.role });
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setState({
          phase: "error",
          message: err instanceof Error ? err.message : String(err),
        });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.phase === "loading") {
    return (
      <main className={styles.page}>
        <div className={styles.skeleton} aria-label="正在验证身份">
          正在验证身份…
        </div>
      </main>
    );
  }

  if (state.phase === "anonymous") {
    return (
      <main className={styles.page}>
        <div className={styles.notice}>
          <div className={styles.noticeTitle}>需要登录</div>
          <p className={styles.noticeText}>
            Creator Studio 仅限 analyst / admin 角色使用,请先登录。
          </p>
          <Link href="/login?next=/studio" className={styles.cta}>
            前往登录
          </Link>
        </div>
      </main>
    );
  }

  if (state.phase === "forbidden") {
    return (
      <main className={styles.page}>
        <div className={styles.notice}>
          <div className={styles.noticeTitle}>无权访问</div>
          <p className={styles.noticeText}>
            Creator Studio 仅限 analyst / admin 角色使用。当前账号角色:
            {state.role}。如需权限请联系管理员。
          </p>
          <Link href="/" className={styles.cta}>
            返回首页
          </Link>
        </div>
      </main>
    );
  }

  if (state.phase === "error") {
    return (
      <main className={styles.page}>
        <div className={styles.notice}>
          <div className={styles.noticeTitle}>身份校验失败</div>
          <p className={styles.noticeText}>
            无法连接后端 API,请确认服务已启动后刷新重试。
            <br />
            <span className={styles.errDetail}>{state.message}</span>
          </p>
        </div>
      </main>
    );
  }

  return <>{children}</>;
}
