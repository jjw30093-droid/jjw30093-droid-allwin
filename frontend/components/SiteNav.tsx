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
import { ThemeToggle } from "./ThemeToggle";

const NAV_ITEMS = [
  { href: "/", label: "首页", exact: true, mobile: false },
  { href: "/matches", label: "比赛", mobile: true },
  {
    href: "/leagues",
    label: "联赛数据",
    prefix: "/league",
    mobile: false,
  },
  { href: "/track-record", label: "公开战绩", mobile: false },
  { href: "/about-model", label: "模型说明", mobile: false },
  { href: "/pricing", label: "会员", mobile: true },
  { href: "/about", label: "关于我们", mobile: true },
];

type BottomNavIcon = "home" | "matches" | "record" | "account";

function BottomIcon({ name }: { name: BottomNavIcon }) {
  if (name === "home") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden>
        <path d="M3.5 10.5 12 3.8l8.5 6.7v9.2h-6v-5.8h-5v5.8h-6z" />
      </svg>
    );
  }
  if (name === "matches") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden>
        <path d="M5 4.5h14v15H5zM8 8h8M8 12h5M8 16h7" />
      </svg>
    );
  }
  if (name === "record") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden>
        <path d="M4 19.5h16M6.5 17V11h3v6zM10.5 17V6h3v11zM14.5 17V9h3v8z" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" aria-hidden>
      <circle cx="12" cy="8" r="3.5" />
      <path d="M5.5 20c.5-4 2.7-6 6.5-6s6 2 6.5 6" />
    </svg>
  );
}

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
  const bottomItems: {
    href: string;
    label: string;
    icon: BottomNavIcon;
    active: boolean;
  }[] = [
    { href: "/", label: "首页", icon: "home", active: pathname === "/" },
    {
      href: "/matches",
      label: "比赛",
      icon: "matches",
      active: pathname.startsWith("/matches"),
    },
    {
      href: "/track-record",
      label: "战绩",
      icon: "record",
      active: pathname.startsWith("/track-record"),
    },
    {
      href: me?.authenticated ? "/account" : "/login",
      label: "我的",
      icon: "account",
      active: pathname.startsWith("/account") || pathname.startsWith("/login"),
    },
  ];

  return (
    <>
      <header className={styles.header}>
        <div className={styles.inner}>
          <Link href="/" className={styles.brand}>
            <span className={styles.brandLine} aria-hidden="true" />
            <span className={styles.brandNames}>
              <span className={styles.brandMark}>欧赢</span>
              <span className={styles.brandSub}>ALLWIN</span>
            </span>
            <span className={styles.brandDescriptor}>足球数据与比赛分析</span>
          </Link>
          <nav className={styles.nav} aria-label="主导航">
            {NAV_ITEMS.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={isActive(item) ? styles.active : styles.link}
                data-mobile={item.mobile ? "show" : "hide"}
              >
                {item.label}
              </Link>
            ))}
            {(role === "analyst" || role === "admin") && (
              <Link
                href="/studio"
                className={pathname.startsWith("/studio") ? styles.active : styles.link}
                data-mobile="hide"
              >
                Studio
              </Link>
            )}
            {role === "admin" && (
              <Link
                href="/admin"
                className={pathname.startsWith("/admin") ? styles.active : styles.link}
                data-mobile="hide"
              >
                管理
              </Link>
            )}
          </nav>
          <div className={styles.actions}>
            <ThemeToggle />
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
                  账户
                </Link>
              )}
            </div>
          </div>
        </div>
      </header>

      <nav
        className={styles.bottomNav}
        aria-label="手机底部导航"
        data-testid="mobile-bottom-nav"
      >
        {bottomItems.map((item) => (
          <Link
            key={item.label}
            href={item.href}
            className={item.active ? styles.bottomActive : styles.bottomLink}
            aria-current={item.active ? "page" : undefined}
          >
            <BottomIcon name={item.icon} />
            <span>{item.label}</span>
          </Link>
        ))}
      </nav>
    </>
  );
}
