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
 *
 * 2026-09 改版为横条形态(参照 miaomiaodi.cc 的 VipPromoBanner):每条腿一行,
 * 左边联赛徽 + 两队队徽与队名 + 开球时刻,右边推荐选项;串关的多条腿靠一根
 * 竖轨收成一组共用一个标签。整块底部一枚带扫光的金色 CTA 胶囊。
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { LeagueBadge } from "@/components/matches/LeagueBadge";
import {
  MARKET_ZH,
  beijingDateKey,
  formatBeijingHM,
  formatBeijingZh,
} from "@/components/matches/zh";
import { TeamBadge } from "@/components/teams/TeamBadge";
import { comboLabel, visiblePublicPicks } from "@/lib/reco-banner";
import type { GetJson } from "@/lib/api-v1";
import styles from "@/app/page.module.css";

type CurrentResp = GetJson<"/api/v1/reco/public/current">;
export type PublicPickSlip = CurrentResp["slips"][number];
type PublicPickLeg = PublicPickSlip["legs"][number];

const REFRESH_INTERVAL_MS = 60_000;

/**
 * 一条腿的展示行。
 *
 * 有结构化比赛事实(联赛 + 主客队)时画徽标,否则退回录入时的 match_desc
 * 文本——缺图标只是少画一个图标,绝不因此把腿藏起来(后端那几个字段全部
 * 可空且缺失即退化,见 backend/queries/reco.py::_banner_match_facts_for_ids)。
 *
 * 刻意**不展示赔率**:banner 的产品要求就是不出赔率,后端在 SQL 层就没
 * SELECT 那一列,这里连可选渲染都不写。
 */
function LegRow({ leg, slipDate }: { leg: PublicPickLeg; slipDate: string }) {
  const structured = leg.home && leg.away;
  // 北京时间(§11.2:赛程语境默认 UTC+8)。**同日只出钟点,跨日才带日期**:
  // banner 窗口是 2 天(覆盖跨零点的深夜场),一律写 "03:00" 分不清是哪天。
  // 用 slip_date 与开球的北京自然日比对来二选一——两个字符串比较,不读时钟,
  // 服务端与客户端结果恒等,没有水合风险。
  // kickoff_at_utc 可空是合法状态(§6.2.1),缺就不画这一段,不用 Date 顶替。
  const kickoff = leg.kickoff_at_utc
    ? beijingDateKey(leg.kickoff_at_utc) === slipDate
      ? formatBeijingHM(leg.kickoff_at_utc)
      : formatBeijingZh(leg.kickoff_at_utc)
    : null;
  const market = MARKET_ZH[leg.market] ?? leg.market;

  return (
    <span className={styles.picksLeg}>
      {/* 第一行:联赛徽 + 两枚队徽 + 完整队名。
          2026-09-04 生产真机实测:单行塞进"联赛徽 + 双队徽 + 双队名 + vs +
          时间 + 选项"后,固定开销 171px、只剩 16px 给两个队名,「皇家贝蒂斯」
          被压到 **7px**(等于看不见)。本地测的是 3 字队名 + 同日钟点 + 短选项,
          真实数据在队名长度、跨日日期、选项长度三个维度上同时更重,单行结构
          不成立。算过所有单行补救(去联赛徽 19px、去 vs 17px、时间只留钟点
          31px)全做也只腾出 83px,5 字队名仍要截——所以折成两行。 */}
      <span className={styles.picksLegTeams}>
        {structured ? (
          <>
            {leg.league_id != null && (
              <LeagueBadge leagueId={leg.league_id} size={14} />
            )}
            <TeamBadge
              teamName={leg.home!.name}
              crestUrl={leg.home!.crest_url}
              size={24}
            />
            <span className={styles.picksLegTeam}>{leg.home!.name}</span>
            <span className={styles.picksLegVs}>vs</span>
            <TeamBadge
              teamName={leg.away!.name}
              crestUrl={leg.away!.crest_url}
              size={24}
            />
            <span className={styles.picksLegTeam}>{leg.away!.name}</span>
          </>
        ) : (
          <span className={styles.picksLegDesc}>{leg.match_desc}</span>
        )}
      </span>

      {/* 第二行:左边时间与玩法(次级),右边推荐选项(主角)。
          玩法名回到可见文案——两行结构腾出了空间,不必再退到 title 里。
          `?? leg.market` 兜底必须保留:market 是自由文本不锁枚举。 */}
      <span className={styles.picksLegMeta}>
        <span className={styles.picksLegMetaLeft}>
          {kickoff && (
            <time className={styles.picksLegTime} dateTime={leg.kickoff_at_utc!}>
              {kickoff}
            </time>
          )}
          <span className={styles.picksLegMarket}>{market}</span>
        </span>
        {/* selection 本身已是中文展示文案("主胜"/"大3.5"),原样渲染,严禁解析。 */}
        <span className={styles.picksLegPick}>{leg.selection}</span>
      </span>
    </span>
  );
}

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

  const legCount = visible.reduce((n, s) => n + s.legs.length, 0);

  return (
    <Link
      href="/reco?tab=public"
      className={styles.picksStrip}
      aria-label="今日公推,查看完整内容"
    >
      <span className={styles.picksStripHead}>
        <span className={styles.picksChip}>
          <span className={styles.picksChipDot} aria-hidden />
          今日公推
        </span>
        <span className={styles.picksStripMeta}>
          {visible.length} 单 {legCount} 场
        </span>
      </span>

      <span className={styles.picksStripSlips}>
        {visible.map((slip) => (
          <span key={slip.id} className={styles.picksSlipRow}>
            <span className={styles.picksSlipLabel}>
              {comboLabel(slip.legs.length)}
            </span>
            <span className={styles.picksSlipRail} aria-hidden />
            <span className={styles.picksSlipLegs}>
              {slip.legs.map((leg) => (
                <LegRow key={leg.id} leg={leg} slipDate={slip.slip_date} />
              ))}
            </span>
          </span>
        ))}
      </span>

      <span className={styles.picksCtaRow}>
        <span className={styles.picksStripCta}>
          <span className={styles.picksCtaShine} aria-hidden />
          <span>看今日公推</span>
          <span className={styles.picksCtaArrow} aria-hidden>
            →
          </span>
        </span>
      </span>
    </Link>
  );
}
