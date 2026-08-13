"use client";

/**
 * 首页"今晚/明天/未来7天"计数条 + 重点比赛卡 + 近期比赛列表。
 *
 * 和 MatchListLive(见 components/matches/MatchListLive.tsx)同一个根因、
 * 同一套修法:会话 cookie Path=/api/v1,Next RSC 读不到,所以首页服务端渲染
 * 永远按匿名口径拉 /api/v1/matches——不只是"小联赛比赛看不到"这么简单,
 * 连"今日重点"选哪一场都是在匿名可见的联赛集合里选的,登录后本该能选中
 * 小联赛的比赛(小联赛几乎天天有赛程,数据富集度未必比五大联赛差),
 * 结果永远还是在英超西甲这几个里面挑。
 *
 * 挂载后浏览器带 cookie 重新拉一次同一组请求,用 selectHomepageMatches
 * (lib/homepage.ts)重新算一遍"今日重点"——这是纯函数,服务端 SSR 和这里
 * 用的是同一份判据,不会出现"登录前后选场逻辑不一致"。
 *
 * 静默降级:刷新失败时保留 SSR 渲染的匿名内容,不整块报错。
 *
 * 「暂无已排期的未来比赛」也要能被客户端刷新纠正:匿名可见的联赛集合在
 * 赛季间歇期可能真的一场没有,但登录后可见的联赛(几乎全年都在踢)大概率
 * 有——如果只在服务端判一次"没有就摆烂",登录也救不回来。
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { clientFetch, type MatchListResponse } from "@/lib/api-v1";
import { selectHomepageMatches, type HomeMatchCard } from "@/lib/homepage";
import { KickoffCountdown } from "@/components/matches/KickoffCountdown";
import { LocalTime } from "@/components/matches/LocalTime";
import { LeagueBadge } from "@/components/matches/LeagueBadge";
import { WinProbabilityBar } from "@/components/matches/WinProbabilityBar";
import { TeamBadge } from "@/components/teams/TeamBadge";
import { LEAGUE_ZH, beijingDateKey, formatBeijingZh } from "@/components/matches/zh";
import styles from "@/app/page.module.css";

type Counts = { today: number; tomorrow: number; week: number } | null;

interface Props {
  initialFeatured: HomeMatchCard | null;
  initialWeekly: HomeMatchCard[];
  initialCounts: Counts;
  initialErrored: boolean;
}

async function fetchHomeData(): Promise<{
  featured: HomeMatchCard | null;
  weekly: HomeMatchCard[];
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
  const { featured, ordered } = selectHomepageMatches(cards, new Date(), { withShots });
  const weekly = ordered
    .filter((card) => card.match.match_id !== featured?.match.match_id)
    .sort((a, b) =>
      (a.match.kickoff_at_utc ?? a.match.date_utc).localeCompare(
        b.match.kickoff_at_utc ?? b.match.date_utc,
      ),
    );

  const counts: Counts =
    todayList && tomorrowList
      ? { today: todayList.total, tomorrow: tomorrowList.total, week: upcoming.total }
      : null;

  return { featured, weekly, counts };
}

/* ── 今晚/明天/未来7天计数条 ────────────────────────────── */

function CountItem({ label, count, href }: { label: string; count: number; href: string }) {
  const inner = (
    <>
      <span className={styles.countLabel}>{label}</span>
      <b className={`${styles.countNum} num`}>{count}</b>
      <span className={styles.countUnit}>场</span>
    </>
  );
  return count > 0 ? (
    <Link href={href} className={styles.countItem}>
      {inner}
    </Link>
  ) : (
    <span className={`${styles.countItem} ${styles.countItemEmpty}`}>{inner}</span>
  );
}

/* ── 重点比赛视觉卡 ─────────────────────────────────────── */

function featuredKicker(card: HomeMatchCard): string {
  const kickoff = card.match.kickoff_at_utc;
  if (kickoff) {
    const key = beijingDateKey(kickoff);
    const now = Date.now();
    const todayKey = beijingDateKey(new Date(now).toISOString());
    const tomorrowKey = beijingDateKey(new Date(now + 86_400_000).toISOString());
    if (key && key === todayKey) return "今日重点";
    if (key && key === tomorrowKey) return "明日重点";
  }
  return "近期重点";
}

function FeaturedMatchCard({ card }: { card: HomeMatchCard }) {
  const { match } = card;
  // 「数据更新于」现在特指赔率快照时间,不是笼统的"数据"——概率条本身就是
  // 这份赔率折算出来的,时间戳必须精确对应它,不能拿一个不相关的时间戳
  // 顶替(CLAUDE.md §6.2 不伪装)。没有赔率时才退回比赛记录本身的更新时间。
  const oddsObservedAt = match.win_probability?.observed_at ?? null;

  return (
    <section className={styles.heroSection} aria-labelledby="featured-match-title">
      <article className={styles.heroCard} data-testid="featured-match-card">
        <header className={styles.heroHeader}>
          <div>
            <p className={styles.heroKicker}>{featuredKicker(card)}</p>
            <p className={styles.heroKickoff}>
              {match.kickoff_at_utc ? (
                <LocalTime iso={match.kickoff_at_utc} fallback={match.date_utc} />
              ) : (
                match.date_utc
              )}
              {match.kickoff_at_utc && (
                <span className={styles.heroCountdown}>
                  <KickoffCountdown iso={match.kickoff_at_utc} />
                </span>
              )}
            </p>
          </div>
        </header>

        <div className={styles.heroTeams}>
          <h1 id="featured-match-title">
            <span className={styles.heroTeam}>
              <TeamBadge teamName={match.home.name} crestUrl={match.home.crest_url} size={48} eager />
              <span>{match.home.name}</span>
            </span>
            <span className={styles.heroVsCol}>
              <span className={styles.heroLeague}>
                <LeagueBadge leagueId={match.league_id} size={14} />
                {LEAGUE_ZH[match.league_id] ?? `联赛 ${match.league_id}`}
              </span>
              <b>vs</b>
            </span>
            <span className={styles.heroTeam}>
              <TeamBadge teamName={match.away.name} crestUrl={match.away.crest_url} size={48} eager />
              <span>{match.away.name}</span>
            </span>
          </h1>
        </div>

        {match.win_probability ? (
          <div className={styles.winProbSection}>
            <WinProbabilityBar probability={match.win_probability} />
          </div>
        ) : match.requires_login ? (
          <p className={styles.evidenceEmpty}>
            登录后查看胜平负概率——「{LEAGUE_ZH[match.league_id] ?? `联赛 ${match.league_id}`}」需要登录访问。
          </p>
        ) : (
          <p className={styles.evidenceEmpty}>本场暂无赔率数据,无法折算胜平负概率。</p>
        )}

        <footer className={styles.heroFooter}>
          <p>
            {oddsObservedAt ? (
              <>
                赔率采集于 <LocalTime iso={oddsObservedAt} />
              </>
            ) : match.data_updated_at ? (
              <>
                数据更新于 <LocalTime iso={match.data_updated_at} />
              </>
            ) : (
              "以当前公开数据为准"
            )}
          </p>
          <Link
            href={`/matches/${match.match_id}`}
            className={styles.primaryAction}
            aria-label={`查看${match.home.name}对${match.away.name}完整分析`}
          >
            查看完整分析
            <span aria-hidden>→</span>
          </Link>
        </footer>
      </article>
    </section>
  );
}

/* ── 近期比赛(按北京时间日期分组,组内按开球时间顺序) ─────── */

// 首屏只留 4-5 张卡,减少上下长度——横滑天然承载"还有更多"这件事,
// 不需要像竖排列表那样把全部近期比赛都堆在首屏(站长要求)。
const SECONDARY_CARD_LIMIT = 5;

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
        <p className={styles.secondaryPending}>
          {match.requires_login ? "登录后查看概率" : "暂无赔率数据"}
        </p>
      )}
    </Link>
  );
}

function ThisWeekSection({ cards }: { cards: HomeMatchCard[] }) {
  const items = cards.slice(0, SECONDARY_CARD_LIMIT);

  return (
    <section
      className={styles.secondarySection}
      aria-labelledby="this-week-title"
      data-testid="this-week-matches"
    >
      <header className={styles.secondaryHead}>
        <div>
          <h2 id="this-week-title">近期比赛</h2>
          {items.length > 0 && <span>{items.length}</span>}
        </div>
        {items.length > 0 && <span className={styles.secondaryHint}>左右滑动查看更多</span>}
      </header>
      {items.length === 0 ? (
        <p className={styles.secondaryEmpty}>未来 7 天暂无其他已排期比赛。</p>
      ) : (
        <div className={styles.secondaryViewport}>
          {items.map((card) => (
            <SecondaryMatchCard key={card.match.match_id} card={card} />
          ))}
          <Link href="/matches" className={styles.secondaryAll}>
            <strong>显示更多</strong>
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
  initialWeekly,
  initialCounts,
  initialErrored,
}: Props) {
  const [featured, setFeatured] = useState(initialFeatured);
  const [weekly, setWeekly] = useState(initialWeekly);
  const [counts, setCounts] = useState(initialCounts);
  const [errored, setErrored] = useState(initialErrored);

  useEffect(() => {
    let cancelled = false;
    fetchHomeData()
      .then((fresh) => {
        if (cancelled) return;
        setFeatured(fresh.featured);
        setWeekly(fresh.weekly);
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
  if (!featured) {
    return <div className={styles.errorBox}>暂无已排期的未来比赛。</div>;
  }

  const earliest = featured ?? weekly[0] ?? null;

  return (
    <>
      {counts && (
        <div className={styles.countsBar}>
          <div className={styles.countsRow} data-testid="match-counts-bar">
            <CountItem label="今晚" count={counts.today} href="/matches?window=today" />
            <CountItem label="明天" count={counts.tomorrow} href="/matches?window=tomorrow" />
            <CountItem label="未来7天" count={counts.week} href="/matches" />
          </div>
          {counts.today === 0 && counts.tomorrow === 0 && earliest && (
            <p className={styles.countsFallback}>
              今明暂无可用比赛。最近比赛:
              {earliest.match.kickoff_at_utc ? (
                <LocalTime iso={earliest.match.kickoff_at_utc} fallback={earliest.match.date_utc} />
              ) : (
                earliest.match.date_utc
              )}{" "}
              {earliest.match.home.name} vs {earliest.match.away.name}
              <Link href={`/matches/${earliest.match.match_id}`}>查看最近比赛 →</Link>
            </p>
          )}
        </div>
      )}

      <FeaturedMatchCard card={featured} />
      <ThisWeekSection cards={weekly} />
    </>
  );
}
