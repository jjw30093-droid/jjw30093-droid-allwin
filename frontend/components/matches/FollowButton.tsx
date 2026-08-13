"use client";

/**
 * 详情页「关注比赛」按钮:首版存浏览器本地(lib/followed-matches),
 * 首页「我关注的比赛」模块读取同一份数据。SSR 阶段不知道本地状态,
 * 挂载后再渲染真实状态,避免水合不一致。
 */

import { useEffect, useState } from "react";
import { isFollowed, toggleFollowed } from "@/lib/followed-matches";
import styles from "./FollowButton.module.css";

export function FollowButton({ matchId }: { matchId: number }) {
  const [followed, setFollowed] = useState<boolean | null>(null);

  useEffect(() => {
    // 经微任务回调触发,effect 体内不同步 setState(react-hooks/set-state-in-effect)
    let cancelled = false;
    void Promise.resolve().then(() => {
      if (!cancelled) setFollowed(isFollowed(matchId));
    });
    return () => {
      cancelled = true;
    };
  }, [matchId]);

  if (followed === null) return null;

  return (
    <button
      type="button"
      className={followed ? styles.btnOn : styles.btn}
      aria-pressed={followed}
      onClick={() => setFollowed(toggleFollowed(matchId))}
    >
      {followed ? "✓ 已关注" : "☆ 关注比赛"}
    </button>
  );
}
