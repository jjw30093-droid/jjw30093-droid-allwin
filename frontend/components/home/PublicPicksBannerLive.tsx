"use client";

/**
 * 首页「每日公推」banner 的客户端渲染层(2026-09)。
 *
 * 它只负责两件事:按**各自浏览器的当前时间**把已到点的公推撤下,以及渲染。
 * 取数与首轮过滤在服务端组件 PublicPicksBanner.tsx。
 *
 * 水合安全(本组件最容易写错的一点):初始 state 必须是 null,首帧原样渲染
 * 服务端传来的列表,逐字节等同 SSR 输出;now 只在 useEffect 里设置。若写成
 * useState(() => filter(slips, Date.now())),SSR 的 now 与浏览器的 now 不同,
 * 首帧 HTML 与客户端首次 render 不一致 → React 水合不匹配并整块重渲。这个
 * 问题在本地 dev(SSR 与 CSR 几乎同时发生)往往复现不出来,跨 CDN 缓存后必现。
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { MARKET_ZH } from "@/components/matches/zh";
import { comboLabel, visiblePublicPicks } from "@/lib/reco-banner";
import type { GetJson } from "@/lib/api-v1";
import styles from "@/app/page.module.css";

type CurrentResp = GetJson<"/api/v1/reco/public/current">;
export type PublicPickSlip = CurrentResp["slips"][number];

const REFRESH_INTERVAL_MS = 60_000;

export function PublicPicksBannerLive({
  slips,
  hideAfterHours,
}: {
  slips: PublicPickSlip[];
  hideAfterHours: number;
}) {
  const [nowMs, setNowMs] = useState<number | null>(null);

  useEffect(() => {
    // 首次取 now 经微任务回调触发,effect 体内不同步 setState
    // (react-hooks/set-state-in-effect;同 app/admin/page.tsx 的既有写法)。
    void Promise.resolve().then(() => setNowMs(Date.now()));
    // 每分钟重算一次:页面开着不动,到点也能自动撤下(撤下时刻最多晚 60 秒)。
    const id = setInterval(() => setNowMs(Date.now()), REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, []);

  const visible =
    nowMs === null ? slips : visiblePublicPicks(slips, nowMs, hideAfterHours);

  // 空态:整块不渲染,DOM 里不留任何占位(留空壳会在首屏顶部留下一条
  // 只有 padding/border 的空条)。
  if (visible.length === 0) return null;

  return (
    <Link
      href="/reco?tab=public"
      className={styles.publicPicksBanner}
      aria-label="今日公推,查看完整内容"
    >
      <div className={styles.picksHead}>
        <h2>今日公推</h2>
        <span className={styles.picksBadge}>{visible.length} 单</span>
      </div>

      {visible.map((slip) => (
        <div key={slip.id} className={styles.publicPicksSlip}>
          <div className={styles.publicPicksSlipHead}>
            <span className={styles.publicPicksTitle}>{slip.title}</span>
            <span className={styles.publicPicksCombo}>
              {comboLabel(slip.legs.length)}
            </span>
          </div>
          {slip.legs.map((leg) => (
            <div key={leg.id} className={styles.publicPicksLeg}>
              <span className={styles.publicPicksMatch}>{leg.match_desc}</span>
              {/* selection 本身已是中文展示文案("主胜"/"大2.5"),原样渲染,
                  严禁解析。?? leg.market 兜底必须保留:market 是自由文本不锁
                  枚举,新市场不能把内部枚举值直接露给用户。 */}
              <span className={styles.publicPicksPick}>
                {MARKET_ZH[leg.market] ?? leg.market} · {leg.selection}
              </span>
            </div>
          ))}
        </div>
      ))}

      <span className={styles.publicPicksCta}>查看完整公推（含赔率与思路）→</span>
    </Link>
  );
}
