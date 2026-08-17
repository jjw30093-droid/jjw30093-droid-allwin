"use client";

/**
 * 首页"今晚/明天/未来7天"计数条 + 重点比赛 + 近期比赛。
 *
 * 2026-08-16 权限口径修正:后端对任何人(含匿名)恒返回完整比赛内容,
 * MatchSummary 不再有 `requires_login` 字段——此前"一张免费卡 + 一张锁定
 * 对照卡"并排展示的产品概念随之消失(不存在需要诚实展示"这场看不到概率"
 * 的锁定比赛了),改为单张重点比赛卡 + 近期比赛列表(见
 * lib/homepage.ts::selectFeaturedMatch)。数据更新条(赛程/赔率/推荐三条
 * 时间戳)仍收编在计数条卡内部。
 *
 * 和 MatchListLive(见 components/matches/MatchListLive.tsx)同一个根因、
 * 同一套修法:会话 cookie Path=/api/v1,Next RSC 读不到,所以首页服务端渲染
 * 永远按匿名口径拉 /api/v1/matches;挂载后浏览器带 cookie 重新拉一次同一组
 * 请求,用 selectFeaturedMatch(lib/homepage.ts)重新选一次重点比赛——这是
 * 纯函数,服务端 SSR 和这里用的是同一份判据。由于比赛内容不再区分身份,
 * 这次客户端刷新实际只会在数据本身发生变化时才改变展示结果。
 *
 * 静默降级:刷新失败时保留 SSR 渲染的内容,不整块报错。
 *
 * 数据更新条(freshness)不参与这次客户端刷新:它是公开只读聚合,不随
 * 请求者身份变化,SSR 一次即可,没必要跟着登录状态重新拉。
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { clientFetch, type GetJson, type MatchListResponse } from "@/lib/api-v1";
import { selectFeaturedMatch, type HomeMatchCard } from "@/lib/homepage";
import { KickoffCountdown } from "@/components/matches/KickoffCountdown";
import { LocalTime } from "@/components/matches/LocalTime";
import { LeagueBadge } from "@/components/matches/LeagueBadge";
import { WinProbabilityBar } from "@/components/matches/WinProbabilityBar";
import { TeamBadge } from "@/components/teams/TeamBadge";
import {
  LEAGUE_ZH,
  STATUS_ZH,
  beijingDateKey,
  formatBeijingDateTime,
  formatBeijingHM,
  formatBeijingZh,
} from "@/components/matches/zh";
import { syncStateLabel } from "@/lib/product-status";
import styles from "@/app/page.module.css";

type Counts = { today: number; tomorrow: number; week: number } | null;
type Freshness = GetJson<"/api/v1/status/freshness">;

interface Props {
  initialFeatured: HomeMatchCard | null;
  initialSecondary: HomeMatchCard[];
  initialCounts: Counts;
  initialFreshness: Freshness | null;
  initialErrored: boolean;
}

export async function fetchHomeData(): Promise<{
  featured: HomeMatchCard | null;
  secondary: HomeMatchCard[];
  counts: Counts;
}> {
  const [upcoming, todayList, tomorrowList, shotsList] = await Promise.all([
    // boost=free_predicted:与 app/page.tsx 的 SSR 请求同一个参数、同一个
    // 目的——把"已发布概率"的比赛在服务端顶进 limit=8 截断线以内,不能只看
    // API 原始顺序的前 8 条(见 lib/homepage.ts 顶部注释:这里和 SSR 必须用
    // 同一套判据)。参数名沿用后端既有 query 定义(backend/api/routes_public.py),
    // 与登录态无关,只是"这场比赛是否已算出概率"的服务端筛选提示。
    clientFetch<MatchListResponse>(
      "/api/v1/matches?status=upcoming&window=7d&limit=8&boost=free_predicted",
    ),
    clientFetch<MatchListResponse>(
      "/api/v1/matches?status=upcoming&window=today&limit=1",
    ).catch(() => null),
    clientFetch<MatchListResponse>(
      "/api/v1/matches?status=upcoming&window=tomorrow&limit=1",
    ).catch(() => null),
    clientFetch<MatchListResponse>(
      "/api/v1/matches?status=upcoming&window=7d&content=shots&limit=200",
    ).catch(() => null),
  ]);

  const cards: HomeMatchCard[] = upcoming.matches.map((match) => ({ match, tip: null }));
  const withShots = new Set<number>((shotsList?.matches ?? []).map((m) => m.match_id));
  const { featured, secondary } = selectFeaturedMatch(cards, new Date(), { withShots });

  const counts: Counts =
    todayList && tomorrowList
      ? { today: todayList.total, tomorrow: tomorrowList.total, week: upcoming.total }
      : null;

  return { featured, secondary, counts };
}

/* ── 今晚/明天/未来7天计数条(时间轴式:数字 + 进度段 + 数据更新条) ── */

function CountItem({
  label,
  count,
  href,
  highlight = false,
}: {
  label: string;
  count: number;
  href: string;
  /** "未来7天"格数字非空时用 --brand-teal(§2),与另外两格区分。 */
  highlight?: boolean;
}) {
  const inner = (
    <>
      <span className={styles.countLabel}>{label}</span>
      <b className={`${styles.countNum} num`}>{count}</b>
      <span className={styles.countUnit}>场{count > 0 && " ›"}</span>
    </>
  );
  return count > 0 ? (
    <Link
      href={href}
      className={styles.countItem}
      data-highlight={highlight || undefined}
    >
      {inner}
    </Link>
  ) : (
    <span className={styles.countItem} data-empty="true">
      {inner}
    </span>
  );
}

/** 把一个 UTC 时间戳格式成"今天 HH:mm"(与"现在"同一个北京自然日时)或完整
 * 北京日期时间(跨天时)。formatBeijingHM() 那个只输出 HH:mm 的函数文档
 * 注释里写着"日期永远是今天"——那个假设在这里已经不成立(网络中断可能让
 * 数据好几天没更新),所以这里不能再单独用它,必须先用 beijingDateKey()
 * 比较日期。timestamp 为空或 todayKey 还未算出(挂载前)时回退到完整日期
 * 格式,不冒险显示一个可能错误的"今天"。 */
function smartBeijingTime(iso: string | null | undefined, todayKey: string | null): string {
  if (!iso) return "尚无记录";
  const key = beijingDateKey(iso);
  if (todayKey !== null && key !== null && key === todayKey) {
    const hm = formatBeijingHM(iso);
    if (hm) return `今天 ${hm}`;
  }
  return formatBeijingDateTime(iso) ?? "尚无记录";
}

function FreshnessRow({
  label,
  iso,
  state,
  todayKey,
}: {
  label: string;
  iso: string | null | undefined;
  state: Freshness["schedule_state"];
  todayKey: string | null;
}) {
  return (
    <span className={styles.freshnessItem} data-state={state ?? undefined}>
      <span>
        {label} <time dateTime={iso ?? undefined}>{smartBeijingTime(iso, todayKey)}</time>
      </span>
      <span className={styles.freshnessState}>{syncStateLabel(state)}</span>
    </span>
  );
}

export function FreshnessBlock({ freshness }: { freshness: Freshness | null }) {
  // "今天"的判定依赖客户端此刻的北京日期,挂载前(含 SSR)先按 null 处理——
  // smartBeijingTime 在 todayKey===null 时回退到完整日期,SSR 与首次客户端
  // 渲染的输出因此恒等,不产生水合警告;挂载后这个 effect 把日期填上,
  // 才会把"今天"范围内的行升级成"今天 HH:mm"简写。
  const [todayKey, setTodayKey] = useState<string | null>(null);
  useEffect(() => {
    // setTimeout(0) 而不是挂载时同步调用 setState——避免 effect 内联发起的
    // 级联渲染(react-hooks/set-state-in-effect),与 KickoffCountdown.tsx
    // 同一套写法;0ms 延迟对"今天"这个粒度的判断无感知。
    const id = setTimeout(() => {
      setTodayKey(beijingDateKey(new Date().toISOString()));
    }, 0);
    return () => clearTimeout(id);
  }, []);

  if (!freshness) return null;
  return (
    <p className={styles.freshnessLine} data-testid="freshness-line">
      <FreshnessRow
        label="赛程更新"
        iso={freshness.schedule_updated_at}
        state={freshness.schedule_state}
        todayKey={todayKey}
      />
      <FreshnessRow
        label="赔率更新"
        iso={freshness.odds_updated_at}
        state={freshness.odds_state}
        todayKey={todayKey}
      />
      <FreshnessRow
        label="推荐更新"
        iso={freshness.reco_updated_at}
        state={freshness.reco_state}
        todayKey={todayKey}
      />
    </p>
  );
}

/* ── 开球行:日期时间 + 真实倒计时 ─────────────────────────── */

function KickoffRow({ match }: { match: HomeMatchCard["match"] }) {
  return (
    <p className={styles.pairKickoff}>
      {match.kickoff_at_utc ? (
        <LocalTime iso={match.kickoff_at_utc} fallback={match.date_utc} />
      ) : (
        match.date_utc
      )}
      {match.kickoff_at_utc && (
        <span className={styles.pairCountdown}>
          <KickoffCountdown iso={match.kickoff_at_utc} />
        </span>
      )}
    </p>
  );
}

function CardHeader({ match }: { match: HomeMatchCard["match"] }) {
  return (
    <header className={styles.pairHeader}>
      <span className={styles.pairLeague}>
        <LeagueBadge leagueId={match.league_id} size={18} />
        {LEAGUE_ZH[match.league_id] ?? `联赛 ${match.league_id}`}
        {match.round ? ` · 第${match.round}轮` : ""}
      </span>
      <span className={styles.pairBadges}>
        <span className={styles.pairStatusBadge}>{STATUS_ZH[match.status] ?? match.status}</span>
      </span>
    </header>
  );
}

/* ── 重点比赛卡(2026-08-16 权限口径修正:不再区分免费/锁定,单张卡) ── */

function FeaturedMatchCard({ card }: { card: HomeMatchCard }) {
  const { match } = card;
  const oddsObservedAt = match.win_probability?.observed_at ?? null;

  return (
    <article className={styles.featuredCard} data-testid="featured-match-card">
      <CardHeader match={match} />

      <div className={styles.pairBody}>
        <div className={styles.pairMatchup}>
          <KickoffRow match={match} />
          <div className={styles.pairTeamsInline}>
            <span className={styles.pairTeamInline}>
              <TeamBadge teamName={match.home.name} crestUrl={match.home.crest_url} size={36} eager />
              <span className={styles.pairTeamNameInline}>{match.home.name}</span>
            </span>
            <span className={styles.pairVs} aria-hidden>
              vs
            </span>
            <span className={styles.pairTeamInline}>
              <TeamBadge teamName={match.away.name} crestUrl={match.away.crest_url} size={36} eager />
              <span className={styles.pairTeamNameInline}>{match.away.name}</span>
            </span>
          </div>
        </div>

        <div className={styles.pairProbCol}>
          <p className={styles.pairProbTitle}>胜平负概率</p>
          {match.win_probability ? (
            <>
              <WinProbabilityBar probability={match.win_probability} size="lg" />
              <p className={styles.pairSourceLine}>
                Bet365 赔率去水折算 · 采集于{" "}
                {oddsObservedAt && <LocalTime iso={oddsObservedAt} />}
              </p>
              {match.odds_coverage_tier === "full_timeline" && (
                <p className={styles.pairTierLine}>该场有完整赔率走势</p>
              )}
              {match.odds_coverage_tier === "open_close_only" && (
                <p className={styles.pairTierLine}>该场只有开盘/收盘两点</p>
              )}
            </>
          ) : (
            <p className={styles.pairEmptyNote}>本场暂无赔率数据,无法折算胜平负概率。</p>
          )}
        </div>
      </div>

      <Link
        href={`/matches/${match.match_id}`}
        className={styles.pairCta}
        aria-label={`查看${match.home.name}对${match.away.name}完整分析`}
      >
        查看完整分析
        <span aria-hidden>→</span>
      </Link>
    </article>
  );
}

/* ── 近期比赛 ────────────────────────────────────────────── */

// 单份列表最多渲染 7 张(桌面 4x2 网格的上限);<900px 两档(横滑 5 张 /
// 640-899px 2x3 网格 5 张)靠纯 CSS 的 :nth-child 隐藏第 6、7 张卡实现,
// 不为不同断点渲染两份重复 DOM(§落地清单:纯 CSS 媒体查询,不做 JS 测宽)。
const SECONDARY_CARD_LIMIT = 7;

function SecondaryMatchCard({ card }: { card: HomeMatchCard }) {
  const { match } = card;
  const kickoffLabel = match.kickoff_at_utc ? formatBeijingZh(match.kickoff_at_utc) : null;
  return (
    <Link
      href={`/matches/${match.match_id}`}
      className={styles.secondaryCard}
      aria-label={`查看${match.home.name}对${match.away.name}完整分析`}
    >
      <div className={styles.secondaryMeta}>
        <span>{kickoffLabel ?? match.date_utc}</span>
        <span>
          <LeagueBadge leagueId={match.league_id} size={14} />
          {LEAGUE_ZH[match.league_id] ?? `联赛 ${match.league_id}`}
        </span>
      </div>
      <div className={styles.secondaryTeams}>
        <span className={styles.secondaryTeamInline}>
          <TeamBadge teamName={match.home.name} crestUrl={match.home.crest_url} size={24} />
          <strong className={styles.secondaryTeamName}>{match.home.name}</strong>
        </span>
        <span className={styles.secondaryVs} aria-hidden>
          vs
        </span>
        <span className={styles.secondaryTeamInline}>
          <TeamBadge teamName={match.away.name} crestUrl={match.away.crest_url} size={24} />
          <strong className={styles.secondaryTeamName}>{match.away.name}</strong>
        </span>
      </div>
      {match.win_probability ? (
        <div className={styles.secondaryProb}>
          <WinProbabilityBar probability={match.win_probability} compact />
        </div>
      ) : (
        <p className={styles.secondaryPending}>暂无赔率数据</p>
      )}
    </Link>
  );
}

function ThisWeekSection({ cards, total }: { cards: HomeMatchCard[]; total: number }) {
  const items = cards.slice(0, SECONDARY_CARD_LIMIT);

  return (
    <section
      className={styles.secondarySection}
      aria-labelledby="this-week-title"
      data-testid="this-week-matches"
    >
      <header className={styles.secondaryHead}>
        <h2 id="this-week-title">近期比赛</h2>
        {items.length > 0 && (
          <>
            <span className={styles.secondaryHintMobile}>左右滑动 · 共 {total} 场</span>
            <Link href="/matches" className={styles.secondaryHintDesktop}>
              全部 {total} 场 <span aria-hidden>→</span>
            </Link>
          </>
        )}
      </header>
      {items.length === 0 ? (
        <p className={styles.secondaryEmpty}>未来 7 天暂无其他已排期比赛。</p>
      ) : (
        // 容器自身可横向滚动(<640px),body 不产生横向滚动条;640-899px 与
        // ≥900px 两档改纯 CSS 网格(第 6、7 张卡 <900px 下用 :nth-child 隐藏)
        <div className={styles.secondaryViewport}>
          {items.map((card) => (
            <SecondaryMatchCard key={card.match.match_id} card={card} />
          ))}
          <Link href="/matches" className={styles.secondaryAll}>
            <strong>全部 {total} 场</strong>
            <span className={styles.secondaryAllHint}>按日期、联赛筛选</span>
            <span aria-hidden>→</span>
          </Link>
        </div>
      )}
    </section>
  );
}

/* ── 组装 ───────────────────────────────────────────────── */

export function HomeMatchExperienceLive({
  initialFeatured,
  initialSecondary,
  initialCounts,
  initialFreshness,
  initialErrored,
}: Props) {
  const [featured, setFeatured] = useState(initialFeatured);
  const [secondary, setSecondary] = useState(initialSecondary);
  const [counts, setCounts] = useState(initialCounts);
  const [errored, setErrored] = useState(initialErrored);

  useEffect(() => {
    let cancelled = false;
    fetchHomeData()
      .then((fresh) => {
        if (cancelled) return;
        setFeatured(fresh.featured);
        setSecondary(fresh.secondary);
        setCounts(fresh.counts);
        setErrored(false);
      })
      .catch(() => {
        // 保留 SSR 的渲染结果,不覆盖成错误态。
      });
    return () => {
      cancelled = true;
    };
    // 首页只在 SSR 时确定一次初始数据,挂载时刷新一次即可;
    // fetchHomeData 不闭包任何外部依赖,依赖数组本来就是空的。
  }, []);

  if (errored) {
    return <div className={styles.errorBox}>今日比赛暂时无法加载，请稍后再试。</div>;
  }
  if (!featured) {
    return <div className={styles.errorBox}>暂无已排期的未来比赛。</div>;
  }

  return (
    <>
      {counts && (
        <div className={styles.countsBar}>
          <div className={styles.countsRow} data-testid="match-counts-bar">
            <CountItem label="今晚" count={counts.today} href="/matches?window=today" />
            <CountItem label="明天" count={counts.tomorrow} href="/matches?window=tomorrow" />
            <CountItem
              label="未来 7 天"
              count={counts.week}
              href="/matches"
              highlight
            />
          </div>
          <div className={styles.countsProgress}>
            <span className={counts.today > 0 ? styles.progressOn : styles.progressOff} />
            <span className={counts.tomorrow > 0 ? styles.progressOn : styles.progressOff} />
            <span className={counts.week > 0 ? styles.progressOn : styles.progressOff} />
          </div>
          {counts.today === 0 && counts.tomorrow === 0 && (
            <p className={styles.countsFallback}>
              今晚和明天没有已排期的比赛。最近一场在{" "}
              {featured.match.kickoff_at_utc ? (
                <LocalTime iso={featured.match.kickoff_at_utc} fallback={featured.match.date_utc} />
              ) : (
                featured.match.date_utc
              )}
              。
            </p>
          )}
          <FreshnessBlock freshness={initialFreshness} />
        </div>
      )}

      <section className={styles.pairSection} aria-labelledby="hero-pair-title">
        <header className={styles.pairSectionHead}>
          <h2 id="hero-pair-title">重点比赛</h2>
        </header>
        <FeaturedMatchCard card={featured} />
      </section>

      <ThisWeekSection cards={secondary} total={counts?.week ?? secondary.length} />
    </>
  );
}
