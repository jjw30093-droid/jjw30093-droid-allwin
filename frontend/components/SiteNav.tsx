"use client";

/**
 * 全站统一导航。公共 HTML 不因登录态变化(利于共享缓存):
 * 登录态在浏览器端经 /api/v1/me 私有请求水合(宪法 §10.2)。
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { getMe, type MeResponse } from "@/lib/api-v1";
import styles from "./SiteNav.module.css";

const NAV_ITEMS = [
  { href: "/", label: "首页", exact: true },
  { href: "/matches", label: "比赛" },
  { href: "/league/47/standings", label: "联赛数据", prefix: "/league" },
  { href: "/track-record", label: "公开战绩" },
  { href: "/about-model", label: "模型说明" },
  { href: "/pricing", label: "会员" },
];

export function SiteNav() {
  const pathname = usePathname();
  const [me, setMe] = useState<MeResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    getMe()
      .then((data) => {
        if (!cancelled) setMe(data);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [pathname]);

  const isActive = (item: (typeof NAV_ITEMS)[number]) => {
    if (item.exact) return pathname === item.href;
    const prefix = item.prefix ?? item.href;
    return pathname.startsWith(prefix);
  };

  const role = me?.authenticated ? me.user?.role : null;

  return (
    <header className={styles.header}>
      <div className={styles.inner}>
        <Link href="/" className={styles.brand}>
          <span className={styles.brandMark}>欧赢</span>
          <span className={styles.brandSub}>allwin</span>
        </Link>
        <nav className={styles.nav} aria-label="主导航">
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={isActive(item) ? styles.active : styles.link}
            >
              {item.label}
            </Link>
          ))}
          {(role === "analyst" || role === "admin") && (
            <Link
              href="/studio"
              className={pathname.startsWith("/studio") ? styles.active : styles.link}
            >
              Studio
            </Link>
          )}
          {role === "admin" && (
            <Link
              href="/admin"
              className={pathname.startsWith("/admin") ? styles.active : styles.link}
            >
              管理
            </Link>
          )}
        </nav>
        <div className={styles.account}>
          {me?.authenticated ? (
            <Link href="/account" className={styles.accountLink}>
              <span className={styles.planBadge} data-plan={me.plan}>
                {me.plan === "premium" ? "Premium" : me.plan === "pro" ? "Pro" : "免费"}
              </span>
              {me.user?.display_name}
            </Link>
          ) : (
            <Link href="/login" className={styles.loginBtn}>
              登录
            </Link>
          )}
        </div>
      </div>
    </header>
  );
}
