"use client";

/**
 * 首页"今晚/明天/未来7天"计数条 + 重点位(免费/锁定对照卡) + 近期比赛。
 *
 * 2026-08-13 Claude Design 定稿重排(design_handoff_home_hero/README.md):
 * 单张 featured 卡拆成并排两张——一张免费卡(优先选真的有概率的免费比赛)+
 * 一张锁定对照卡(同一批比赛里开球最近的一场需登录比赛),让"免费能看到
 * 什么、登录后多看到什么"一眼对比,而不是靠猜。数据更新条(赛程/赔率/
 * 推荐三条时间戳)从页面顶部独立一行收编进计数条卡内部。
 *
 * 和 MatchListLive(见 components/matches/MatchListLive.tsx)同一个根因、
 * 同一套修法:会话 cookie Path=/api/v1,Next RSC 读不到,所以首页服务端渲染
 * 永远按匿名口径拉 /api/v1/matches——登录后可见联赛集合是匿名集合的超集,
 * 挂载后浏览器带 cookie 重新拉一次同一组请求,用 selectHeroPair
 * (lib/homepage.ts)重新算一遍两张重点卡该显示谁——这是纯函数,服务端 SSR
 * 和这里用的是同一份判据,不会出现"登录前后选场逻辑不一致"。
 *
 * 静默降级:刷新失败时保留 SSR 渲染的匿名内容,不整块报错。
 *
 * 数据更新条(freshness)不参与这次客户端刷新:它是公开只读聚合,不随
 * 请求者身份变化,SSR 一次即可,没必要跟着登录状态重新拉。
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { clientFetch, type GetJson, type MatchListResponse } from "@/lib/api-v1";
import { selectHeroPair, type HomeMatchCard } from "@/lib/homepage";
import { KickoffCountdown } from "@/components/matches/KickoffCountdown";
import { LocalTime } from "@/components/matches/LocalTime";
import { LeagueBadge } from "@/components/matches/LeagueBadge";
import { WinProbabilityBar } from "@/components/matches/WinProbabilityBar";
import { TeamBadge } from "@/components/teams/TeamBadge";
import { LEAGUE_ZH, STATUS_ZH, formatBeijingHM, formatBeijingZh } from "@/components/matches/zh";
import styles from "@/app/page.module.css";

type Counts = { today: number; tomorrow: number; week: number } | null;
type Freshness = GetJson<"/api/v1/status/freshness">;

interface Props {
  initialFreeCard: HomeMatchCard | null;
  initialLockedCard: HomeMatchCard | null;
  initialSecondary: HomeMatchCard[];
  initialCounts: Counts;
  initialFreshness: Freshness | null;
  initialErrored: boolean;
}

async function fetchHomeData(): Promise<{
  freeCard: HomeMatchCard | null;
  lockedCard: HomeMatchCard | null;
  secondary: HomeMatchCard[];
  counts: Counts;
}> {
  const [upcoming, todayList, tomorrowList, shotsList] = await Promise.all([
    clientFetch<MatchListResponse>("/api/v1/matches?status=upcoming&window=7d&limit=8"),
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
  const { freeCard, lockedCard, secondary } = selectHeroPair(cards, new Date(), { withShots });

  const counts: Counts =
    todayList && tomorrowList
      ? { today: todayList.total, tomorrow: tomorrowList.total, week: upcoming.total }
      : null;

  return { freeCard, lockedCard, secondary, counts };
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

function FreshnessBlock({ freshness }: { freshness: Freshness | null }) {
  if (!freshness) return null;
  const hm = (iso: string | null | undefined) => {
    if (!iso) return "尚无记录";
    return formatBeijingHM(iso) ?? "尚无记录";
  };
  return (
    <p className={styles.freshnessLine} data-testid="freshness-line">
      <span>
        赛程更新 <time dateTime={freshness.schedule_updated_at ?? undefined}>{hm(freshness.schedule_updated_at)}</time>
      </span>
      <span>
        赔率更新 <time dateTime={freshness.odds_updated_at ?? undefined}>{hm(freshness.odds_updated_at)}</time>
      </span>
      <span>
        推荐更新 <time dateTime={freshness.reco_updated_at ?? undefined}>{hm(freshness.reco_updated_at)}</time>
      </span>
    </p>
  );
}

/* ── 开球行(免费卡/锁定卡共用):日期时间 + 真实倒计时 ─────────── */

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

function CardHeader({
  match,
  tierBadge,
}: {
  match: HomeMatchCard["match"];
  tierBadge: { text: string; free: boolean };
}) {
  return (
    <header className={styles.pairHeader}>
      <span className={styles.pairLeague}>
        <LeagueBadge leagueId={match.league_id} size={18} />
        {LEAGUE_ZH[match.league_id] ?? `联赛 ${match.league_id}`}
        {match.round ? ` · 第${match.round}轮` : ""}
      </span>
      <span className={styles.pairBadges}>
        <span className={styles.pairStatusBadge}>{STATUS_ZH[match.status] ?? match.status}</span>
        <span className={tierBadge.free ? styles.pairFreeBadge : styles.pairLockedBadge}>
          {tierBadge.text}
        </span>
      </span>
    </header>
  );
}

/* ── 重点位:免费卡 ──────────────────────────────────────── */

function FeaturedFreeCard({ card }: { card: HomeMatchCard }) {
  const { match } = card;
  const oddsObservedAt = match.win_probability?.observed_at ?? null;

  return (
    <article className={styles.freeCard} data-testid="featured-match-card">
      <CardHeader match={match} tierBadge={{ text: "免费", free: true }} />

      <div className={styles.pairBody}>
        <div className={styles.pairMatchup}>
          <KickoffRow match={match} />
          <div className={styles.pairTeamRow}>
            <TeamBadge teamName={match.home.name} crestUrl={match.home.crest_url} size={48} eager />
            <span className={styles.pairTeamName}>
              {match.home.name}
              <b>主</b>
            </span>
          </div>
          <div className={styles.pairTeamRow}>
            <TeamBadge teamName={match.away.name} crestUrl={match.away.crest_url} size={48} eager />
            <span className={styles.pairTeamName}>
              {match.away.name}
              <b>客</b>
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
        className={styles.pairFreeCta}
        aria-label={`查看${match.home.name}对${match.away.name}完整分析`}
      >
        查看完整分析
        <span aria-hidden>→</span>
      </Link>
    </article>
  );
}

/* ── 重点位:锁定对照卡 ──────────────────────────────────── */

function FeaturedLockedCard({ card }: { card: HomeMatchCard }) {
  const { match } = card;
  const leagueName = LEAGUE_ZH[match.league_id] ?? `联赛 ${match.league_id}`;
  const kickoffLabel = match.kickoff_at_utc
    ? formatBeijingZh(match.kickoff_at_utc)
    : match.date_utc;

  return (
    <article className={styles.lockedCard}>
      <CardHeader match={match} tierBadge={{ text: "需登录", free: false }} />

      <div className={styles.pairBody}>
        <div className={styles.pairMatchup}>
          <KickoffRow match={match} />
          <div className={styles.pairTeamRow}>
            <TeamBadge teamName={match.home.name} crestUrl={match.home.crest_url} size={48} />
            <span className={styles.pairTeamName}>
              {match.home.name}
              <b>主</b>
            </span>
          </div>
          <div className={styles.pairTeamRow}>
            <TeamBadge teamName={match.away.name} crestUrl={match.away.crest_url} size={48} />
            <span className={styles.pairTeamName}>
              {match.away.name}
              <b>客</b>
            </span>
          </div>
        </div>
      </div>

      <p className={styles.pairLockedNote}>
        <span className={styles.pairLockedNoteDesktop}>
          左边那张的三段概率条,这场没有——「{leagueName}」的胜平负概率不对未登录用户下发。
          登录后这块位置会出现同样的概率条、赔率走势和深度报告。
        </span>
        <span className={styles.pairLockedNoteMobile}>
          {kickoffLabel} 开球。该联赛的胜平负概率不对未登录用户下发,登录后才会计算。
        </span>
      </p>

      <Link
        href={`/login?next=${encodeURIComponent(`/matches/${match.match_id}`)}`}
        className={styles.pairLockedCta}
      >
        登录后查看这场概率 <span aria-hidden>→</span>
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
        <strong className={styles.secondaryTeamName}>{match.home.name}</strong>
        <span className={styles.secondaryVs} aria-hidden>
          vs
        </span>
        <strong className={styles.secondaryTeamName}>{match.away.name}</strong>
      </div>
      {match.win_probability ? (
        <div className={styles.secondaryProb}>
          <WinProbabilityBar probability={match.win_probability} compact />
        </div>
      ) : (
        <p className={styles.secondaryPending} data-tone={match.requires_login ? "link" : "muted"}>
          {match.requires_login ? "登录后查看概率 →" : "暂无赔率数据"}
        </p>
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
  initialFreeCard,
  initialLockedCard,
  initialSecondary,
  initialCounts,
  initialFreshness,
  initialErrored,
}: Props) {
  const [freeCard, setFreeCard] = useState(initialFreeCard);
  const [lockedCard, setLockedCard] = useState(initialLockedCard);
  const [secondary, setSecondary] = useState(initialSecondary);
  const [counts, setCounts] = useState(initialCounts);
  const [errored, setErrored] = useState(initialErrored);

  useEffect(() => {
    let cancelled = false;
    fetchHomeData()
      .then((fresh) => {
        if (cancelled) return;
        setFreeCard(fresh.freeCard);
        setLockedCard(fresh.lockedCard);
        setSecondary(fresh.secondary);
        setCounts(fresh.counts);
        setErrored(false);
      })
      .catch(() => {
        // 保留 SSR 的匿名口径渲染,不覆盖成错误态。
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
  if (!freeCard && !lockedCard) {
    return <div className={styles.errorBox}>暂无已排期的未来比赛。</div>;
  }

  const earliest = freeCard ?? lockedCard;
  const bothPresent = Boolean(freeCard && lockedCard);

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
          {counts.today === 0 && counts.tomorrow === 0 && earliest && (
            <p className={styles.countsFallback}>
              今晚和明天没有已排期的比赛。最近一场在{" "}
              {earliest.match.kickoff_at_utc ? (
                <LocalTime iso={earliest.match.kickoff_at_utc} fallback={earliest.match.date_utc} />
              ) : (
                earliest.match.date_utc
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
          {bothPresent ? (
            <>
              <span className={styles.pairHintMobile}>1 场免费 · 1 场需登录</span>
              <span className={styles.pairHintDesktop}>左边这场免费 · 右边这场需登录</span>
            </>
          ) : freeCard ? (
            <span className={styles.pairHint}>本场免费查看</span>
          ) : (
            <span className={styles.pairHint}>本场需登录查看</span>
          )}
        </header>
        <div className={bothPresent ? styles.pairGrid : styles.pairGridSolo}>
          {freeCard && <FeaturedFreeCard card={freeCard} />}
          {lockedCard && <FeaturedLockedCard card={lockedCard} />}
        </div>
      </section>

      <ThisWeekSection cards={secondary} total={counts?.week ?? secondary.length} />
    </>
  );
}
