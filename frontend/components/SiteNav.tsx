"use client";

/**
 * 全站统一导航。公共 HTML 不因登录态变化(利于共享缓存):
 * 登录态在浏览器端经 /api/v1/me 私有请求水合(宪法 §10.2)。
 */

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
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
  { href: "/reco", label: "每日精选", mobile: true },
  { href: "/track-record", label: "模型战绩", mobile: false },
  { href: "/about-model", label: "模型说明", mobile: false },
  { href: "/pricing", label: "权限说明", mobile: true },
  { href: "/about", label: "关于我们", mobile: true },
];

type BottomNavIcon = "home" | "matches" | "picks" | "record" | "account";

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
  if (name === "picks") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden>
        <path d="M12 3.6 14.5 9l5.8.5-4.4 3.9 1.3 5.7L12 16.1 6.8 19.1l1.3-5.7-4.4-3.9L9.5 9z" />
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

/**
 * 底部导航(手机):首页|比赛|精选|战绩|我的。
 * 「精选/战绩」都指向 /reco 的两个标签,选中态需要读 ?tab=,
 * 因此拆出本组件由 Suspense 包裹(useSearchParams 的 Next 16 约束);
 * SSR fallback 用 tab=null 渲染同一结构,水合后补上标签级选中态。
 */
function BottomNavLinks({
  pathname,
  tab,
  authed,
}: {
  pathname: string;
  tab: string | null;
  authed: boolean;
}) {
  const onReco = pathname.startsWith("/reco");
  const items: {
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
      href: "/reco?tab=daily",
      label: "精选",
      icon: "picks",
      active: onReco && tab !== "record",
    },
    {
      href: "/reco?tab=record",
      label: "战绩",
      icon: "record",
      active: onReco && tab === "record",
    },
    {
      href: authed ? "/account" : "/login",
      label: "我的",
      icon: "account",
      active: pathname.startsWith("/account") || pathname.startsWith("/login"),
    },
  ];
  return (
    <nav
      className={styles.bottomNav}
      aria-label="手机底部导航"
      data-testid="mobile-bottom-nav"
    >
      {items.map((item) => (
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
  );
}

function BottomNavWithTab(props: { pathname: string; authed: boolean }) {
  const searchParams = useSearchParams();
  return <BottomNavLinks {...props} tab={searchParams.get("tab")} />;
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
  const authed = Boolean(me?.authenticated);

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
                  {me.user?.display_name ?? "已登录"}
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

      <Suspense fallback={<BottomNavLinks pathname={pathname} tab={null} authed={authed} />}>
        <BottomNavWithTab pathname={pathname} authed={authed} />
      </Suspense>
    </>
  );
}
