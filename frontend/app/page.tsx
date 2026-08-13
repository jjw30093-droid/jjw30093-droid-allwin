import { cache, Suspense } from "react";
import Link from "next/link";
import {
  serverGet,
  serverGetOptional,
  type GetJson,
  type MatchListResponse,
} from "@/lib/api-v1";
import { selectHomepageMatches, type HomeMatchCard } from "@/lib/homepage";
import { LocalTime } from "@/components/matches/LocalTime";
import { FollowedMatches } from "@/components/matches/FollowedMatches";
import { RecentlyViewed } from "@/components/matches/RecentlyViewed";
import { HomeMatchExperienceLive } from "@/components/home/HomeMatchExperienceLive";
import styles from "./page.module.css";

type RecoOverview = GetJson<"/api/v1/reco/overview">;
type Freshness = GetJson<"/api/v1/status/freshness">;

function SectionSkeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className={styles.skeleton} aria-hidden>
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} className={styles.skelLine} />
      ))}
    </div>
  );
}

type HomePageData = {
  cards: HomeMatchCard[];
  featured: HomeMatchCard | null;
  weekly: HomeMatchCard[];
  counts: { today: number; tomorrow: number; week: number } | null;
};

/**
 * 首页比赛与预测的唯一服务端聚合入口。
 * React cache 让首屏各模块在同一次渲染中共享同一份请求结果。
 *
 * 不再拉 /matches/{id}/analysis:重点卡的胜平负概率条直接读
 * match.win_probability(随 /api/v1/matches 列表一次性下发,见
 * backend/queries/odds.py::latest_1x2_by_match),不需要再单独请求
 * analysis bundle——那是"近期战绩"面板专用的字段,面板已下架。
 */
const getHomePageData = cache(async (): Promise<HomePageData> => {
  const [upcoming, todayList, tomorrowList, shotsList] = await Promise.all([
    serverGet<MatchListResponse>(
      "/api/v1/matches?status=upcoming&window=7d&limit=8",
      { revalidate: 60 },
    ),
    serverGetOptional<MatchListResponse>(
      "/api/v1/matches?status=upcoming&window=today&limit=1",
      { revalidate: 60 },
    ).catch(() => null),
    serverGetOptional<MatchListResponse>(
      "/api/v1/matches?status=upcoming&window=tomorrow&limit=1",
      { revalidate: 60 },
    ).catch(() => null),
    // 重点比赛选场需要知道"哪几场点进去真有东西看"。一次列表请求换回整段窗口
    // 的射门史命中集合,比逐场拉 analysis 便宜得多(替代了此前逐场拉 prediction
    // 的 N 次请求 —— 概率面板已下架,那些请求本就没有消费方了)。
    serverGetOptional<MatchListResponse>(
      "/api/v1/matches?status=upcoming&window=7d&content=shots&limit=200",
      { revalidate: 60 },
    ).catch(() => null),
  ]);

  const cards = upcoming.matches.map((match) => ({ match, tip: null }));

  const withShots = new Set<number>(
    (shotsList?.matches ?? []).map((m) => m.match_id),
  );
  const { featured, ordered } = selectHomepageMatches(cards, new Date(), {
    withShots,
  });
  // 近期比赛列表:按开球时间顺序(不套用 featured 的"时间就近"排序),去掉重点场
  const weekly = [...cards]
    .filter((card) => card.match.match_id !== featured?.match.match_id)
    .sort((a, b) =>
      (a.match.kickoff_at_utc ?? a.match.date_utc).localeCompare(
        b.match.kickoff_at_utc ?? b.match.date_utc,
      ),
    );

  const counts =
    todayList && tomorrowList
      ? {
          today: todayList.total,
          tomorrow: tomorrowList.total,
          week: upcoming.total,
        }
      : null;

  return { cards: ordered, featured, weekly, counts };
});

const getRecoOverview = cache(async (): Promise<RecoOverview | null> => {
  return serverGetOptional<RecoOverview>("/api/v1/reco/overview", {
    revalidate: 120,
  }).catch(() => null);
});

const getFreshness = cache(async (): Promise<Freshness | null> => {
  return serverGetOptional<Freshness>("/api/v1/status/freshness", {
    revalidate: 60,
  }).catch(() => null);
});

/* ── 今日更新状态 ───────────────────────────────────────── */

async function FreshnessLine() {
  const f = await getFreshness();
  if (!f) return null;
  // 三条来源各自独立轮询,互不代表彼此;任一为空如实展示"尚无记录",
  // 不用当前时间或另一条的时间顶替。
  return (
    <p className={styles.freshnessLine} data-testid="freshness-line">
      赛程更新 <LocalTime iso={f.schedule_updated_at} fallback="尚无记录" />
      {" ｜ "}
      赔率更新 <LocalTime iso={f.odds_updated_at} fallback="尚无记录" />
      {" ｜ "}
      推荐更新 <LocalTime iso={f.reco_updated_at} fallback="尚无记录" />
    </p>
  );
}

/* ── 今晚/明天/未来7天计数条 + 重点比赛卡 + 近期比赛 ──────────
 * 服务端只算一次匿名口径的初始数据(SSR/无 JS 兜底),真正的"这次访问者
 * 实际能看到什么"交给 HomeMatchExperienceLive 挂载后用 cookie 刷新——
 * 会话 cookie Path=/api/v1,这里(Next RSC)读不到,见该组件顶部注释。
 * 三块以前分开 Suspense(CountsBar / HomeMatchExperience),现在合并成
 * 一次请求 + 一个客户端边界:三块共享同一个 featured 选场结果,分开刷新
 * 会出现"计数条已经是新数据、重点卡还是旧的"这种不一致。 */

async function HomeMatchExperienceSection() {
  let data: HomePageData;
  try {
    data = await getHomePageData();
  } catch {
    return (
      <HomeMatchExperienceLive
        initialFeatured={null}
        initialWeekly={[]}
        initialCounts={null}
        initialErrored
      />
    );
  }

  return (
    <HomeMatchExperienceLive
      initialFeatured={data.featured}
      initialWeekly={data.weekly}
      initialCounts={data.counts}
      initialErrored={false}
    />
  );
}

/* ── 今日精选 + 推荐战绩摘要(匿名聚合,不含单据内容) ───── */

async function DailyPicksSection() {
  const overview = await getRecoOverview();
  return (
    <section className={styles.picksCard} aria-labelledby="daily-picks-title">
      <header className={styles.picksHead}>
        <h2 id="daily-picks-title">今日精选</h2>
        {overview && overview.today_published_count > 0 && (
          <span className={styles.picksBadge}>
            已发布 {overview.today_published_count} 场
          </span>
        )}
      </header>
      {!overview ? (
        <p className={styles.picksNote}>精选状态暂时无法加载,可直接进入精选页查看。</p>
      ) : overview.today_published_count > 0 ? (
        <p className={styles.picksNote}>
          今天已发布 <b className="num">{overview.today_published_count}</b> 场
          {overview.today_latest_published_at && (
            <>
              ,更新于 <LocalTime iso={overview.today_latest_published_at} />
            </>
          )}
          。内容包含赛果方向、数据依据与风险提示。
        </p>
      ) : (
        <p className={styles.picksNote}>今日精选尚未发布;发布后本模块自动更新。</p>
      )}
      {/* 首页只保留重点卡"查看完整分析"一个最强按钮,这里降为文字链接 */}
      <div className={styles.picksActions}>
        <Link href="/reco?tab=daily" className={styles.picksLink}>
          查看今日精选 →
        </Link>
        <Link href="/reco?tab=record" className={styles.picksLink}>
          查看历史战绩 →
        </Link>
      </div>
    </section>
  );
}

async function RecoSummarySection() {
  const overview = await getRecoOverview();
  if (!overview) return null;
  const hasRecords = overview.settled_count > 0;
  return (
    <section className={styles.recoSummary} aria-labelledby="reco-summary-title">
      <header className={styles.sectionHead}>
        <div>
          <h2 id="reco-summary-title" className={styles.sectionTitle}>
            近{overview.window_days}天推荐记录
          </h2>
        </div>
        <Link href="/reco?tab=record" className={styles.textLink}>
          查看全部记录 →
        </Link>
      </header>
      {hasRecords ? (
        <div className={styles.recoSummaryRow}>
          <div className={styles.recoSummaryItem}>
            <b className="num">{overview.settled_count}</b>
            <span>已结算</span>
          </div>
          <div className={styles.recoSummaryItem}>
            <b className="num">
              {overview.win_count}胜 {overview.lose_count}负 {overview.push_count}走
            </b>
            <span>命中/未中/走水</span>
          </div>
          <div className={styles.recoSummaryItem}>
            <b className="num">
              {overview.net_units >= 0 ? "+" : ""}
              {overview.net_units.toFixed(2)}
            </b>
            <span>净单位</span>
          </div>
          {overview.voided_count > 0 && (
            <div className={styles.recoSummaryItem}>
              <b className="num">{overview.voided_count}</b>
              <span>作废(单列)</span>
            </div>
          )}
        </div>
      ) : (
        <p className={styles.emptyText}>
          正式推荐尚未开始,首场结算后开始累计;命中与未中都会保留。
        </p>
      )}
    </section>
  );
}

export default function Home() {
  return (
    <main className={styles.page}>
      <Suspense fallback={null}>
        <FreshnessLine />
      </Suspense>

      <Suspense fallback={<SectionSkeleton lines={5} />}>
        <HomeMatchExperienceSection />
      </Suspense>

      <Suspense fallback={<SectionSkeleton lines={2} />}>
        <DailyPicksSection />
      </Suspense>

      <Suspense fallback={null}>
        <RecoSummarySection />
      </Suspense>

      <FollowedMatches />
      <RecentlyViewed />

      <nav className={styles.quickLinks} aria-label="常用入口">
        <Link href="/track-record">
          <strong>模型公开记录</strong>
          <span>查看发布与赛后评估</span>
        </Link>
        <Link href="/pricing">
          <strong>权限说明</strong>
          <span>登录免费,精选需授权</span>
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
