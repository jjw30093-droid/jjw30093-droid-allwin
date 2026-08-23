"use client";

/**
 * 首页「继续看」(2026-08-23 首页信息架构重排):合并原来各自独立的
 * 「我关注的比赛」「最近浏览」两个 section——都是"回来接着看什么"这一件事,
 * 没必要拆成两块各带一个标题。
 *
 * 两个子组件各自独立发起客户端请求(见 FollowedMatches.tsx / RecentlyViewed.tsx
 * 顶部注释),这里只是拿到它们各自"有没有内容"的回报来决定要不要显示外层
 * 的"继续看"标题——两组数据都为空时,整个区块不渲染(不展示"你还没有关注
 * 任何比赛"这种说教式空状态)。
 *
 * 用 hidden 属性而不是整体 return null:两个子组件必须挂载才能发起请求、
 * 算出"有没有数据",在算出结果前不能不渲染它们。hidden 对视觉和无障碍树
 * 的效果等同于不渲染。
 */

import { useState } from "react";
import { FollowedMatches } from "@/components/matches/FollowedMatches";
import { RecentlyViewed } from "@/components/matches/RecentlyViewed";
import styles from "./ContinueWatching.module.css";

export function ContinueWatching() {
  const [followedVisible, setFollowedVisible] = useState(false);
  const [recentVisible, setRecentVisible] = useState(false);
  const anyVisible = followedVisible || recentVisible;

  return (
    <section
      className={styles.section}
      aria-labelledby="continue-watching-title"
      hidden={!anyVisible}
    >
      <h2 id="continue-watching-title" className={styles.title}>
        继续看
      </h2>
      <div className={styles.groups}>
        <FollowedMatches variant="embedded" onVisibilityChange={setFollowedVisible} />
        <RecentlyViewed variant="embedded" onVisibilityChange={setRecentVisible} />
      </div>
    </section>
  );
}
