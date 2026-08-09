import { cache, Suspense } from "react";
import Link from "next/link";
import {
  serverGet,
  serverGetOptional,
  type MatchListResponse,
  type PredictionResponse,
  type TrackRecordResponse,
} from "@/lib/api-v1";
import {
  HOMEPAGE_FEATURE_EVENT,
  publicRecordView,
  selectFeaturedOverride,
  selectHomepageEvidence,
  selectHomepageMatches,
  type AnalysisBundle,
  type HomeMatchCard,
} from "@/lib/homepage";
import { LocalTime } from "@/components/matches/LocalTime";
import { MatchRow, type FreeTip } from "@/components/matches/MatchRow";
import { TeamBadge } from "@/components/teams/TeamBadge";
import { LEAGUE_ZH, OUTCOME_ZH, pct } from "@/components/matches/zh";
import { syncStateLabel } from "@/lib/product-status";
import styles from "./page.module.css";

function ErrorBox({ text }: { text: string }) {
  return <div className={styles.errorBox}>{text}</div>;
}

function SectionSkeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className={styles.skeleton} aria-hidden>
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} className={styles.skelLine} />
      ))}
    </div>
  );
}

function freeTipOf(resp: PredictionResponse | null): FreeTip | null {
  if (!resp?.available || !resp.prediction) return null;
  const prediction = resp.prediction;
  if (prediction.tier !== "free") return null;
  if (
    !Number.isFinite(prediction.top_probability) ||
    prediction.top_probability < 0 ||
    prediction.top_probability > 1
  ) {
    return null;
  }
  return {
    top_outcome: prediction.top_outcome,
    top_probability: prediction.top_probability,
    probability_source: prediction.meta.probability_source,
  };
}

function probabilitySourceLabel(source: FreeTip["probability_source"]) {
  if (source === "MARKET_BASELINE") return "市场去水基线";
  if (source === "MODEL") return "模型概率";
  return "来源待确认";
}

type HomePageData = {
  cards: HomeMatchCard[];
  featured: HomeMatchCard | null;
  secondary: HomeMatchCard[];
  finished: MatchListResponse | null;
  analysis: AnalysisBundle | null;
};

/**
 * 首页比赛、预测与分析的唯一服务端聚合入口。
 * React cache 让首屏与近期列表在同一次渲染中共享同一份请求结果。
 */
const getHomePageData = cache(async (): Promise<HomePageData> => {
  const [upcoming, finished, ...fallbackLeagueLists] = await Promise.all([
    serverGet<MatchListResponse>("/api/v1/matches?status=upcoming&window=7d&limit=8", {
      revalidate: 60,
    }),
    serverGet<MatchListResponse>("/api/v1/matches?status=finished&limit=5", {
      revalidate: 60,
    }).catch(() => null),
    // 2026-08-07 J 联赛开幕一次性置顶(见 lib/homepage.ts
    // HOMEPAGE_FEATURE_EVENT)所需的北欧回退候选——瑞典超/挪超的比赛
    // 未必落在上面按开球时间排的默认前 8 场里,需要单独取。过期后随
    // selectFeaturedOverride 一起删除。
    ...HOMEPAGE_FEATURE_EVENT.fallbackLeagueIds.map((leagueId) =>
      serverGetOptional<MatchListResponse>(
        `/api/v1/matches?league_id=${leagueId}&status=upcoming&window=7d&limit=3`,
        { revalidate: 60 },
      ).catch(() => null),
    ),
  ]);

  const nordicMatches = fallbackLeagueLists
    .flatMap((resp) => resp?.matches ?? [])
    .filter((m) => !upcoming.matches.some((u) => u.match_id === m.match_id));

  const allMatches = [...upcoming.matches, ...nordicMatches];
  const predictionResponses = await Promise.all(
    allMatches.map((match) =>
      serverGetOptional<PredictionResponse>(
        `/api/v1/matches/${match.match_id}/prediction`,
        { revalidate: 60 },
      ).catch(() => null),
    ),
  );

  const allCards = allMatches.map((match, index) => ({
    match,
    tip: freeTipOf(predictionResponses[index]),
  }));
  const cards = allCards.slice(0, upcoming.matches.length);
  const nordicCards = allCards.slice(upcoming.matches.length);

  const { featured: defaultFeatured, ordered } = selectHomepageMatches(cards);
  const overrideFeatured = selectFeaturedOverride(allCards, nordicCards, new Date());
  const featured = overrideFeatured ?? defaultFeatured;
  const secondary = ordered.filter(
    (card) => card.match.match_id !== featured?.match.match_id,
  );

  const analysis = featured
    ? await serverGetOptional<AnalysisBundle>(
        `/api/v1/matches/${featured.match.match_id}/analysis`,
        { revalidate: 60 },
      ).catch(() => null)
    : null;

  return { cards: ordered, featured, secondary, finished, analysis };
});

function FeaturedMatchCard({
  card,
  analysis,
}: {
  card: HomeMatchCard;
  analysis: AnalysisBundle | null;
}) {
  const { match, tip } = card;
  const evidence = selectHomepageEvidence(analysis?.evidence);
  const updatedAt = match.data_updated_at ?? analysis?.built_at ?? null;

  return (
    <section className={styles.heroSection} aria-labelledby="featured-match-title">
      <article className={styles.heroCard} data-testid="featured-match-card">
        <header className={styles.heroHeader}>
          <div>
            <p className={styles.heroKicker}>
              今日重点
              <span>{LEAGUE_ZH[match.league_id] ?? `联赛 ${match.league_id}`}</span>
            </p>
            <p className={styles.heroKickoff}>
              {match.kickoff_at_utc ? (
                <LocalTime iso={match.kickoff_at_utc} fallback={match.date_utc} />
              ) : (
                match.date_utc
              )}
            </p>
          </div>
          <span className={styles.syncBadge} data-state={match.sync_state ?? "UNKNOWN"}>
            {syncStateLabel(match.sync_state)}
          </span>
        </header>

        <div className={styles.heroTeams}>
          <h1 id="featured-match-title">
            <span className={styles.heroTeam}>
              <TeamBadge
                teamName={match.home.name}
                crestUrl={match.home.crest_url}
                size={48}
                eager
              />
              <span>{match.home.name}</span>
            </span>
            <b>vs</b>
            <span className={styles.heroTeam}>
              <TeamBadge
                teamName={match.away.name}
                crestUrl={match.away.crest_url}
                size={48}
                eager
              />
              <span>{match.away.name}</span>
            </span>
          </h1>
        </div>

        <div className={styles.heroGrid}>
          <div className={styles.conclusionPanel}>
            <span className={styles.conclusionLabel}>公开结论</span>
            {tip ? (
              <>
                <div className={styles.conclusionValue}>
                  <span>{OUTCOME_ZH[tip.top_outcome]}</span>
                  <strong className="num">{pct(tip.top_probability)}</strong>
                </div>
                <p className={styles.conclusionSource}>
                  {probabilitySourceLabel(tip.probability_source)}
                </p>
              </>
            ) : (
              <div className={styles.conclusionPending}>
                <strong>分析准备中</strong>
                <span>赛前数据入库后更新</span>
              </div>
            )}
          </div>

          <div className={styles.evidencePanel}>
            <div className={styles.evidenceHead}>
              <h2>本场依据</h2>
              <span>公开信息</span>
            </div>
            {evidence.length > 0 ? (
              <ul className={styles.evidenceList}>
                {evidence.map((item) => (
                  <li key={`${item.kind}-${item.side}`}>
                    <span aria-hidden />
                    <p>{item.text}</p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className={styles.evidenceEmpty}>本场公开依据尚未完成整理。</p>
            )}
          </div>
        </div>

        <footer className={styles.heroFooter}>
          <p>
            {updatedAt ? (
              <>
                数据更新 <LocalTime iso={updatedAt} />
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

function SecondaryMatches({ cards }: { cards: HomeMatchCard[] }) {
  return (
    <section
      className={styles.secondarySection}
      aria-labelledby="secondary-matches-title"
    >
      <header className={styles.secondaryHead}>
        <div>
          <h2 id="secondary-matches-title">其他比赛</h2>
          <span>未来 7 天</span>
        </div>
        <span className={styles.secondaryHint}>右滑查看更多</span>
      </header>

      {cards.length === 0 ? (
        <p className={styles.secondaryEmpty}>暂无其他已排期比赛。</p>
      ) : (
        <div
          className={styles.secondaryViewport}
          data-testid="secondary-match-ticker"
          aria-label="其他比赛横向列表"
        >
          {cards.map(({ match, tip }) => (
            <Link
              key={match.match_id}
              href={`/matches/${match.match_id}`}
              className={styles.secondaryCard}
              aria-label={`查看${match.home.name}对${match.away.name}比赛分析`}
            >
              <div className={styles.secondaryMeta}>
                <span>{LEAGUE_ZH[match.league_id] ?? `联赛 ${match.league_id}`}</span>
                <span>
                  {match.kickoff_at_utc ? (
                    <LocalTime iso={match.kickoff_at_utc} fallback={match.date_utc} />
                  ) : (
                    match.date_utc
                  )}
                </span>
              </div>
              <div className={styles.secondaryTeams}>
                <span className={styles.secondaryTeam}>
                  <TeamBadge
                    teamName={match.home.name}
                    crestUrl={match.home.crest_url}
                    size={24}
                  />
                  <strong>{match.home.name}</strong>
                </span>
                <span className={styles.secondaryTeam}>
                  <TeamBadge
                    teamName={match.away.name}
                    crestUrl={match.away.crest_url}
                    size={24}
                  />
                  <strong>{match.away.name}</strong>
                </span>
              </div>
              {tip ? (
                <div className={styles.secondaryTip}>
                  <span>{OUTCOME_ZH[tip.top_outcome]}</span>
                  <strong className="num">{pct(tip.top_probability)}</strong>
                </div>
              ) : (
                <span className={styles.secondaryPending}>分析准备中</span>
              )}
            </Link>
          ))}
          <Link href="/matches" className={styles.secondaryAll}>
            <strong>全部比赛</strong>
            <span aria-hidden>→</span>
          </Link>
        </div>
      )}
    </section>
  );
}

async function HomeMatchExperience() {
  let data: HomePageData;
  try {
    data = await getHomePageData();
  } catch {
    return <ErrorBox text="今日比赛暂时无法加载，请稍后再试。" />;
  }

  if (!data.featured) {
    return <ErrorBox text="暂无已排期的未来比赛。" />;
  }

  return (
    <>
      <FeaturedMatchCard card={data.featured} analysis={data.analysis} />
      <SecondaryMatches cards={data.secondary} />
    </>
  );
}

type TrackRecordSample = TrackRecordResponse["samples"][number];

function hitRatePlot(samples: TrackRecordSample[]) {
  const settled = [...samples]
    .reverse()
    .filter(
      (sample): sample is TrackRecordSample & { hit: boolean } =>
        typeof sample.hit === "boolean",
    );

  if (settled.length === 0) {
    return { points: "", last: null, count: 0 };
  }

  let hits = 0;
  const chartPoints = settled.map((sample, index) => {
    if (sample.hit) hits += 1;
    const rate = hits / (index + 1);
    const x = settled.length === 1 ? 240 : (index / (settled.length - 1)) * 480;
    const y = 82 - rate * 68;
    return { x, y };
  });

  return {
    points: chartPoints
      .map(({ x, y }) => `${x.toFixed(1)},${y.toFixed(1)}`)
      .join(" "),
    last: chartPoints.at(-1) ?? null,
    count: settled.length,
  };
}

async function PublicRecordSection() {
  let data: TrackRecordResponse | null = null;
  let failed = false;
  try {
    data = await serverGet<TrackRecordResponse>(
      "/api/v1/track-record?limit=40&offset=0",
      { revalidate: 120 },
    );
  } catch {
    failed = true;
  }

  const view = publicRecordView(data, failed);
  const plot =
    view.status === "ready"
      ? hitRatePlot(view.samples)
      : { points: "", last: null, count: 0 };

  return (
    <section className={styles.recordPanel} aria-labelledby="public-record-title">
      <header className={styles.recordHead}>
        <div>
          <p className={styles.recordKicker}>PUBLIC RECORD</p>
          <div className={styles.recordTitleLine}>
            <h2 id="public-record-title" className={styles.recordTitle}>
              公开战绩
            </h2>
            <span className={styles.recordStatus} data-status={view.status}>
              {view.status === "ready"
                ? "连续公开"
                : view.status === "empty"
                  ? "公开验证中"
                  : "暂不可用"}
            </span>
          </div>
        </div>
        <Link href="/track-record" className={styles.recordLink}>
          逐场记录
          <span aria-hidden>→</span>
        </Link>
      </header>

      {view.status === "empty" && (
        <div className={styles.recordMessage}>
          <strong>公开验证中，从首场锁定记录开始</strong>
          <span>发布、命中与未中都会保留。</span>
        </div>
      )}

      {view.status === "error" && (
        <div className={styles.recordMessage} data-testid="public-record-error">
          <strong>公开记录暂时无法加载</strong>
          <span>比赛入口与完整分析仍可正常查看。</span>
        </div>
      )}

      {view.status === "ready" && (
        <div className={styles.recordBody}>
          <div className={styles.recordMetrics}>
            <div className={styles.recordMetric}>
              <span>正式记录</span>
              <strong className={styles.recordMetricValue}>{view.total}</strong>
              <small>场</small>
            </div>
            <div className={styles.recordMetric}>
              <span>命中率</span>
              <strong className={styles.recordMetricValue}>
                {view.accuracy == null
                  ? "—"
                  : `${(view.accuracy * 100).toFixed(1)}%`}
              </strong>
            </div>
            <div className={styles.recordMetric}>
              <span>已结算</span>
              <strong className={styles.recordMetricValue}>{view.evaluated}</strong>
              <small>场</small>
            </div>
          </div>

          <div className={styles.recordChart}>
            <div className={styles.recordChartHead}>
              <span>累计命中率走势</span>
              <span>{plot.count > 0 ? `最近 ${plot.count} 场` : "等待结算"}</span>
            </div>
            <div className={styles.recordPlot}>
              <svg
                viewBox="0 0 480 96"
                preserveAspectRatio="none"
                role="img"
                aria-label={`最近 ${plot.count} 场已结算预测的累计命中率走势`}
              >
                <line x1="0" y1="14" x2="480" y2="14" className={styles.plotGrid} />
                <line x1="0" y1="48" x2="480" y2="48" className={styles.plotGrid} />
                <line
                  x1="0"
                  y1="82"
                  x2="480"
                  y2="82"
                  className={styles.plotBaseline}
                />
                {plot.points && (
                  <polyline points={plot.points} className={styles.plotLine} />
                )}
                {plot.last && (
                  <circle
                    cx={plot.last.x}
                    cy={plot.last.y}
                    r="3.5"
                    className={styles.plotPoint}
                  />
                )}
              </svg>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

async function RecentMatchesSection() {
  let data: HomePageData;
  try {
    data = await getHomePageData();
  } catch {
    return <ErrorBox text="比赛数据暂时无法加载，请稍后再试。" />;
  }

  const upcoming = data.cards
    .filter((card) => card.match.match_id !== data.featured?.match.match_id)
    .slice(0, 5);

  return (
    <div className={styles.twoCol}>
      <div className={styles.card}>
        <h3 className={styles.cardTitle}>近期赛程</h3>
        {upcoming.length === 0 ? (
          <p className={styles.emptyText}>暂无其他已排期比赛。</p>
        ) : (
          upcoming.map(({ match, tip }) => (
            <MatchRow key={match.match_id} match={match} freeTip={tip} />
          ))
        )}
      </div>
      <div className={styles.card}>
        <h3 className={styles.cardTitle}>最近完赛</h3>
        {!data.finished ? (
          <p className={styles.emptyText}>完赛数据暂时无法加载。</p>
        ) : data.finished.matches.length === 0 ? (
          <p className={styles.emptyText}>暂无已完赛的比赛记录。</p>
        ) : (
          data.finished.matches.map((match) => (
            <MatchRow key={match.match_id} match={match} />
          ))
        )}
      </div>
      <p className={styles.sectionFoot}>
        免费展示一项真实概率；来源可能是模型或明确标识的市场去水基线。
      </p>
    </div>
  );
}

export default function Home() {
  return (
    <main className={styles.page}>
      <Suspense fallback={<SectionSkeleton lines={5} />}>
        <HomeMatchExperience />
      </Suspense>

      <Suspense fallback={<SectionSkeleton lines={2} />}>
        <PublicRecordSection />
      </Suspense>

      <section className={styles.matchesSection}>
        <div className={styles.sectionHead}>
          <div>
            <p className={styles.eyebrow}>MATCHES</p>
            <h2 className={styles.sectionTitle}>近期比赛</h2>
          </div>
          <Link href="/matches" className={styles.textLink}>
            全部比赛 →
          </Link>
        </div>
        <Suspense fallback={<SectionSkeleton lines={5} />}>
          <RecentMatchesSection />
        </Suspense>
      </section>

      <nav className={styles.quickLinks} aria-label="常用入口">
        <Link href="/track-record">
          <strong>公开记录</strong>
          <span>查看发布与赛后评估</span>
        </Link>
        <Link href="/pricing">
          <strong>会员权益</strong>
          <span>完整概率与赔率记录</span>
        </Link>
        <Link href="/about">
          <strong>关于我们</strong>
          <span>平台说明与合作</span>
        </Link>
      </nav>

      <footer className={styles.disclaimer}>
        数据与概率仅供研究和内容参考，不构成投注建议；历史表现不代表未来。
      </footer>
    </main>
  );
}
