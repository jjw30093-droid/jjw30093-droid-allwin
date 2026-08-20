"use client";

/**
 * 详情页「关注比赛」按钮(2026-08-21 起真实写后端)。
 *
 * 此前只写 localStorage——按钮点了显示"已关注",但 /api/v1/favorites 全站
 * 零调用方、生产库 favorites 表 0 行,账户页永远是空的(QA 认定的"假成功")。
 * 现在:
 * - 已登录:addFavorite/removeFavorite 真实写后端;**绝不乐观翻转**——
 *   pending → 服务端确认成功才翻转,失败显式报错并保持原状态(原 bug 的
 *   本质就是"点了就变但什么都没发生",乐观 UI + 静默 catch 等于换个形式重犯);
 * - 未登录:点击展开站内登录引导面板(不用 window.confirm,不跳转丢滚动
 *   位置),链接带编码后的 next 回跳路径;
 * - SSR/加载中:渲染同尺寸 disabled 占位按钮,不再 return null(避免水合后
 *   突然多出一个按钮的布局跳动);
 * - 登录态与已关注列表来自 lib/favorites.ts 的模块级缓存,同页多实例共享
 *   一次请求。
 */

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { ApiError, apiErrorMessage } from "@/lib/api-v1";
import { addFavorite, loadFavorites, removeFavorite, resetFavoritesCache } from "@/lib/favorites";
import styles from "./FollowButton.module.css";

type Phase =
  | "loading"
  | "anonymous"
  | "off"
  | "on"
  | "pending-add"
  | "pending-remove"
  | "unavailable";

export function FollowButton({ matchId }: { matchId: number }) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [error, setError] = useState<string | null>(null);
  const [loginPrompt, setLoginPrompt] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    // 经微任务回调触发,effect 体内不同步 setState(react-hooks/set-state-in-effect)
    let cancelled = false;
    void loadFavorites()
      .then((s) => {
        if (cancelled) return;
        if (!s.authenticated) setPhase("anonymous");
        else setPhase(s.ids.includes(matchId) ? "on" : "off");
      })
      .catch(() => {
        // 网络/服务器错误:不知道真实状态就不假装知道;点击可重试
        if (!cancelled) setPhase("unavailable");
      });
    return () => {
      cancelled = true;
    };
  }, [matchId]);

  const onClick = async () => {
    setError(null);
    if (phase === "anonymous") {
      setLoginPrompt((v) => !v);
      return;
    }
    if (phase === "unavailable") {
      // 重试判定登录态
      setPhase("loading");
      resetFavoritesCache();
      try {
        const s = await loadFavorites();
        if (!s.authenticated) setPhase("anonymous");
        else setPhase(s.ids.includes(matchId) ? "on" : "off");
      } catch {
        setPhase("unavailable");
      }
      return;
    }
    if (phase === "off") {
      setPhase("pending-add");
      try {
        await addFavorite(matchId);
        setPhase("on");
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) {
          setPhase("anonymous");
          setLoginPrompt(true);
          return;
        }
        setPhase("off");
        setError(apiErrorMessage(e, "关注失败,请重试"));
      }
      return;
    }
    if (phase === "on") {
      setPhase("pending-remove");
      try {
        await removeFavorite(matchId);
        setPhase("off");
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) {
          setPhase("anonymous");
          setLoginPrompt(true);
          return;
        }
        setPhase("on");
        setError(apiErrorMessage(e, "取消关注失败,请重试"));
      }
    }
  };

  const followedLook = phase === "on" || phase === "pending-remove";
  const busy = phase === "loading" || phase === "pending-add" || phase === "pending-remove";

  return (
    <span className={styles.wrap}>
      <button
        type="button"
        className={followedLook ? styles.btnOn : styles.btn}
        aria-pressed={followedLook}
        aria-busy={busy || undefined}
        disabled={busy}
        onClick={() => void onClick()}
      >
        {followedLook ? "✓ 已关注" : "☆ 关注比赛"}
      </button>
      {loginPrompt && phase === "anonymous" && (
        <span className={styles.panel} role="dialog" aria-label="登录后关注">
          <span className={styles.panelText}>登录后关注会保存到账号,换设备也能看到。</span>
          <a
            className={styles.panelLink}
            href={`/login?next=${encodeURIComponent(pathname)}`}
          >
            前往登录
          </a>
          <button
            type="button"
            className={styles.panelDismiss}
            onClick={() => setLoginPrompt(false)}
          >
            暂不
          </button>
        </span>
      )}
      {error && <span className={styles.error}>{error}</span>}
    </span>
  );
}
