"use client";

/**
 * 全站统一导航。公共 HTML 不因登录态变化(利于共享缓存):
 * 登录态在浏览器端经 /api/v1/me 私有请求水合(宪法 §10.2)。
 */

import Image from "next/image";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { getMe, type MeResponse } from "@/lib/api-v1";
import styles from "./SiteNav.module.css";
import { ThemeToggle } from "./ThemeToggle";

/** 「比赛」与「赛果」是同一条路由 /matches 的两种视图,靠 ?status 区分,
 * 所以选中态不能只看 pathname——两项会同时高亮。带 `status` 字段的项由
 * NavLinks 读 searchParams 精确判定(与下方 BottomNavWithTab 同一套 Next 16
 * Suspense 约定)。 */
const NAV_ITEMS = [
  { href: "/", label: "首页", exact: true, mobile: false },
  { href: "/matches", label: "比赛", mobile: true, status: "upcoming" },
  { href: "/matches?status=finished", label: "赛果", mobile: true, status: "finished" },
  {
    href: "/leagues",
    label: "联赛数据",
    prefix: "/league",
    mobile: false,
  },
  { href: "/reco", label: "每日精选", mobile: true },
  // 2026-09-16 真实用户反馈"看不到战绩":战绩此前只能从 /reco 的 ?tab=record
  // 进,桌面导航里根本没有入口。现在有独立路由了,放在「每日精选」后面
  // ——它就是精选的成绩单,挨着看最自然。
  { href: "/track-record", label: "战绩", mobile: true },
  // 「权限说明」从顶栏移到只留页脚(SiteFooter 里本来就有):.nav 是
  // overflow-x:auto,第 8 项在窄桌面会被推进横向滚动区、等于看不见,
  // 那新加的「战绩」就白加了。这两页都是低频说明页,页脚足够。
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
 * 底部导航(手机):首页|比赛|精选|战绩|我的(未登录时第五项是「登录」)。
 *
 * 2026-09-16 起不再需要读 ?tab=:「战绩」有了独立路由 /track-record,
 * 「精选」也从 ?tab=daily(对匿名是登录墙)改成裸 /reco。两项的选中态现在
 * 都只看 pathname,所以本组件不再依赖 useSearchParams,外层的 Suspense
 * 包装一并去掉。
 */
function BottomNavLinks({
  pathname,
  authed,
}: {
  pathname: string;
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
      // 2026-09-16 修 bug:此前指向 ?tab=daily,匿名用户点这个最显眼的入口
      // 只会撞上一张"登录后能看到你有哪几场"的登录墙,而真正免费的每日公推
      // 在 ?tab=public(也是 /reco 的默认标签页)。裸 /reco 即落公推。
      href: "/reco",
      label: "精选",
      icon: "picks",
      active: onReco,
    },
    {
      href: "/track-record",
      label: "战绩",
      icon: "record",
      active: pathname.startsWith("/track-record"),
    },
    {
      // 未登录时这里写「我的」,用户看不出是登录入口(2026-09-16 真实反馈),
      // 且点进 /account 也只是又一张"前往登录"卡片,白白多一跳。
      href: authed ? "/account" : "/login",
      label: authed ? "我的" : "登录",
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

/** 主导航链接。`status` 是当前 URL 的 ?status 值(SSR fallback 下为 null,
 * 此时按"未开赛"这个默认值判定,与 app/matches/page.tsx 的解析口径一致)。 */
function NavLinks({ pathname, status }: { pathname: string; status: string | null }) {
  const isActive = (item: (typeof NAV_ITEMS)[number]) => {
    if (item.exact) return pathname === item.href;
    if (item.status) {
      // /matches 的两个视图:pathname 相同,靠 status 区分。URL 没带 status
      // 时等同 "upcoming"(与页面解析同口径),不能让两项同时高亮。
      if (pathname !== "/matches") return false;
      return (status ?? "upcoming") === item.status;
    }
    const prefix = item.prefix ?? item.href;
    return pathname.startsWith(prefix);
  };
  return (
    <>
      {NAV_ITEMS.map((item) => (
        <Link
          key={item.href}
          href={item.href}
          className={isActive(item) ? styles.active : styles.link}
          aria-current={isActive(item) ? "page" : undefined}
          data-mobile={item.mobile ? "show" : "hide"}
        >
          {item.label}
        </Link>
      ))}
    </>
  );
}

function NavLinksWithStatus({ pathname }: { pathname: string }) {
  const searchParams = useSearchParams();
  return <NavLinks pathname={pathname} status={searchParams.get("status")} />;
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


  const role = me?.authenticated ? me.user?.role : null;

  return (
    <>
      <header className={styles.header}>
        <div className={styles.inner}>
          <Link href="/" className={styles.brand}>
            <span className={styles.brandLogo}>
              <Image
                src="/brand/logo-badge-white-256.png"
                alt="喵弟数据研究室"
                width={40}
                height={40}
                className={styles.brandLogoImg}
                data-variant="light"
                priority
              />
              <Image
                src="/brand/logo-badge-256.png"
                alt=""
                width={40}
                height={40}
                className={styles.brandLogoImg}
                data-variant="dark"
                priority
              />
            </span>
            <span className={styles.brandNames}>
              <span className={styles.brandMark}>喵弟数据研究室</span>
              <span className={styles.brandDescriptor}>足球数据研究室</span>
            </span>
          </Link>
          <nav className={styles.nav} aria-label="主导航">
            <Suspense fallback={<NavLinks pathname={pathname} status={null} />}>
              <NavLinksWithStatus pathname={pathname} />
            </Suspense>
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
                // 2026-09-16 真实用户反馈"不知道从哪里登录":这里此前写
                // 「账户」,不含"登录"二字,未登录用户看不出这是登录入口。
                <Link href="/login" className={styles.loginBtn}>
                  登录
                </Link>
              )}
            </div>
          </div>
        </div>
      </header>

      <BottomNavLinks pathname={pathname} authed={me?.authenticated === true} />
    </>
  );
}
