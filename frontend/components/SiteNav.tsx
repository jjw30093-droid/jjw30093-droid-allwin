"use client";

/**
 * 全站统一导航。公共 HTML 不因登录态变化(利于共享缓存):
 * 登录态在浏览器端经 /api/v1/me 私有请求水合(宪法 §10.2)。
 */

import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { LEAGUE_COUNT } from "@/components/matches/zh";
import { getMe, type MeResponse } from "@/lib/api-v1";
import { sanitizeReturnTo } from "@/lib/match-links";
import styles from "./SiteNav.module.css";
import { ThemeToggle } from "./ThemeToggle";

/** 「比赛」与「赛果」是同一条路由 /matches 的两种视图,靠 ?status 区分,
 * 所以选中态不能只看 pathname——两项会同时高亮。带 `status` 字段的项由
 * NavLinks 读 searchParams 精确判定(与下方 BottomNavWithTab 同一套 Next 16
 * Suspense 约定)。 */
type NavItem = {
  href: string;
  label: string;
  exact?: boolean;
  mobile: boolean;
  status?: string;
  prefix?: string;
  /** 这些路径下该项同样高亮(如 /track-record 属于「每日精选」) */
  also?: string[];
};

const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "首页", exact: true, mobile: false },
  { href: "/matches", label: "比赛", mobile: true, status: "upcoming" },
  { href: "/matches?status=finished", label: "赛果", mobile: true, status: "finished" },
  {
    href: "/leagues",
    label: "联赛数据",
    prefix: "/league",
    mobile: false,
  },
  // 2026-09-26:「战绩」并入「每日精选」——它就是精选的成绩单,不再单独占一个入口。
  // /track-record 路由保留(旧链接不失效),访问它时「每日精选」保持高亮;
  // 精选页里的「历史战绩」标签展示同样内容。
  { href: "/reco", label: "每日精选", mobile: true, also: ["/track-record"] },
  // 「权限说明」从顶栏移到只留页脚(SiteFooter 里本来就有):.nav 是
  // overflow-x:auto,第 8 项在窄桌面会被推进横向滚动区、等于看不见,
  // 那新加的「战绩」就白加了。这两页都是低频说明页,页脚足够。
  { href: "/about", label: "关于我们", mobile: true },
];

type BottomNavIcon = "home" | "matches" | "leagues" | "picks" | "account";

/** 顶部品牌副标题里的联赛数量:从联赛配置(LEAGUE_ZH,镜像后端 LEAGUE_META)
 * 计算,新增联赛自动跟着变,不写死数字。 */
export { LEAGUE_COUNT };
export const BRAND_DESCRIPTOR = `${LEAGUE_COUNT} 个联赛的数据图表`;

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
  if (name === "leagues") {
    // 奖杯
    return (
      <svg viewBox="0 0 24 24" aria-hidden>
        <path d="M8 4h8v5a4 4 0 0 1-8 0zM8 6H5v1.5A3 3 0 0 0 8 10.5M16 6h3v1.5a3 3 0 0 1-3 3M12 13v4M9 20h6M10 17h4v3h-4z" />
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
 * 底部导航(手机):首页 | 比赛 | 联赛 | 精选 | 我的(2026-09-26)。
 *
 * - 「战绩」不再单独占入口:并入精选页的「历史战绩」标签(/track-record 路由仍可访问,
 *   访问时「精选」保持高亮);
 * - 「我的」两种登录态文案一致:未登录跳 /login,已登录跳 /account。此前(2026-08-23)
 *   曾恒定指向 /account 由该页自己处理匿名态,现改回在导航层分流——未登录点进
 *   /account 只是多一跳登录墙。登录态由 /api/v1/me 在浏览器端水合,SSR 首屏
 *   (未水合)按未登录渲染,公共 HTML 不因登录态变化。
 */
function BottomNavLinks({
  pathname,
  authed,
}: {
  pathname: string;
  authed: boolean;
}) {
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
      href: "/leagues",
      label: "联赛",
      icon: "leagues",
      active: pathname.startsWith("/leagues") || pathname.startsWith("/league/"),
    },
    {
      // 裸 /reco 落在完全公开的「每日公推」(见 app/reco/page.tsx)
      href: "/reco",
      label: "精选",
      icon: "picks",
      active: pathname.startsWith("/reco") || pathname.startsWith("/track-record"),
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

/** 比赛详情页(/matches/{id})顶部栏左侧的返回箭头(仅手机显示,CSS 控制)。
 *  有来源页(同站 referrer 且浏览历史里有上一页)就回来源页,否则回 /matches。
 *  这取代了页面里原来的"← 返回当前筛选结果"文字链接。 */
export function isMatchDetailPath(pathname: string): boolean {
  return /^\/matches\/\d+/.test(pathname);
}

function BackArrow() {
  const router = useRouter();
  const goBack = () => {
    // 1) 列表页写进 ?from= 的来源(含筛选条件);2) 没有就看浏览器历史里的同站来源页;
    // 3) 都没有回 /matches。from 经 sanitizeReturnTo 白名单校验,不会开放重定向。
    const from = new URLSearchParams(window.location.search).get("from") ?? undefined;
    const safeFrom = sanitizeReturnTo(from);
    if (from && safeFrom === from) {
      router.push(safeFrom);
      return;
    }
    const sameSiteReferrer =
      document.referrer !== "" && new URL(document.referrer).origin === window.location.origin;
    if (sameSiteReferrer && window.history.length > 1) {
      router.back();
    } else {
      router.push("/matches");
    }
  };
  return (
    <button type="button" className={styles.backBtn} aria-label="返回" data-testid="header-back" onClick={goBack}>
      <svg viewBox="0 0 24 24" aria-hidden>
        <path d="M15 5 8 12l7 7" />
      </svg>
    </button>
  );
}

/** 主导航链接。`status` 是当前 URL 的 ?status 值(SSR fallback 下为 null,
 * 此时按"未开赛"这个默认值判定,与 app/matches/page.tsx 的解析口径一致)。 */
function NavLinks({ pathname, status }: { pathname: string; status: string | null }) {
  const isActive = (item: NavItem) => {
    if (item.exact) return pathname === item.href;
    if (item.status) {
      // /matches 的两个视图:pathname 相同,靠 status 区分。URL 没带 status
      // 时等同 "upcoming"(与页面解析同口径),不能让两项同时高亮。
      if (pathname !== "/matches") return false;
      return (status ?? "upcoming") === item.status;
    }
    const prefix = item.prefix ?? item.href;
    if (pathname.startsWith(prefix)) return true;
    return (item.also ?? []).some((p) => pathname.startsWith(p));
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
          {isMatchDetailPath(pathname) && <BackArrow />}
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
              <span className={styles.brandDescriptor}>{BRAND_DESCRIPTOR}</span>
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
