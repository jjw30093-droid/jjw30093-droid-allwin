import { cache, Suspense } from "react";
import Link from "next/link";
import {
  serverGet,
  serverGetOptional,
  type GetJson,
  type MatchListResponse,
} from "@/lib/api-v1";
import { selectFeaturedMatch, type HomeMatchCard } from "@/lib/homepage";
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
  featured: HomeMatchCard | null;
  secondary: HomeMatchCard[];
  counts: { today: number; tomorrow: number; week: number } | null;
  freshness: Freshness | null;
};

/**
 * 首页比赛与预测的唯一服务端聚合入口。
 * React cache 让首屏各模块在同一次渲染中共享同一份请求结果。
 *
 * 不再拉 /matches/{id}/analysis:重点位的胜平负概率条直接读
 * match.win_probability(随 /api/v1/matches 列表一次性下发,见
 * backend/queries/odds.py::latest_1x2_by_match),不需要再单独请求
 * analysis bundle——那是"近期战绩"面板专用的字段,面板已下架。
 *
 * 数据更新条(freshness)一并在这里取:2026-08-13 Claude Design 定稿把
 * 它从独立段落挪进了计数条卡内部,不再是页面顶部单独一行——公开只读、
 * 不受身份影响,SSR 一次即可,不需要跟着 HomeMatchExperienceLive 的
 * 客户端 cookie 刷新重新拉。
 */
export const getHomePageData = cache(async (): Promise<HomePageData> => {
  const [upcoming, todayList, tomorrowList, shotsList, freshness] = await Promise.all([
    // boost=free_predicted(2026-08-16):limit=8 只是原始 API 顺序的前 8 条,
    // 完整 7 天窗口里更靠后的比赛没机会进这一页——哪怕它才是唯一一场
    // "免费且已发布概率"的比赛(见 backend/api/routes_public.py::list_matches
    // 同名参数文档)。opt-in 参数把这场比赛在服务端顶进 limit 截断线以内,
    // 不需要把整窗口 ~95 场完整 MatchSummary 都下发到这里再筛。
    serverGet<MatchListResponse>(
      "/api/v1/matches?status=upcoming&window=7d&limit=8&boost=free_predicted",
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
    // 重点位选场需要知道"哪几场点进去真有东西看"。一次列表请求换回整段窗口
    // 的射门史命中集合,比逐场拉 analysis 便宜得多(替代了此前逐场拉 prediction
    // 的 N 次请求 —— 概率面板已下架,那些请求本就没有消费方了)。
    serverGetOptional<MatchListResponse>(
      "/api/v1/matches?status=upcoming&window=7d&content=shots&limit=200",
      { revalidate: 60 },
    ).catch(() => null),
    getFreshness(),
  ]);

  const cards = upcoming.matches.map((match) => ({ match, tip: null }));
  const withShots = new Set<number>(
    (shotsList?.matches ?? []).map((m) => m.match_id),
  );
  const { featured, secondary } = selectFeaturedMatch(cards, new Date(), {
    withShots,
  });

  const counts =
    todayList && tomorrowList
      ? {
          today: todayList.total,
          tomorrow: tomorrowList.total,
          week: upcoming.total,
        }
      : null;

  return { featured, secondary, counts, freshness };
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

/* ── 今晚/明天/未来7天计数条 + 重点比赛 + 近期比赛 ──
 * 服务端算一次初始数据(SSR/无 JS 兜底),HomeMatchExperienceLive 挂载后
 * 用浏览器 cookie 刷新一次同一组请求(会话 cookie Path=/api/v1,这里
 * (Next RSC)读不到,见该组件顶部注释)——由于比赛内容不再区分身份,这次
 * 客户端刷新只会在数据本身发生变化时才改变展示结果。三块以前分开
 * Suspense(CountsBar / HomeMatchExperience),现在合并成一次请求 + 一个
 * 客户端边界:三块共享同一个选场结果,分开刷新会出现"计数条已经是新
 * 数据、重点卡还是旧的"这种不一致。数据更新条(freshness)不跟着这次
 * 客户端刷新——公开只读聚合,不随登录状态变化。 */

async function HomeMatchExperienceSection() {
  let data: HomePageData;
  try {
    data = await getHomePageData();
  } catch {
    return (
      <HomeMatchExperienceLive
        initialFeatured={null}
        initialSecondary={[]}
        initialCounts={null}
        initialFreshness={null}
        initialErrored
      />
    );
  }

  return (
    <HomeMatchExperienceLive
      initialFeatured={data.featured}
      initialSecondary={data.secondary}
      initialCounts={data.counts}
      initialFreshness={data.freshness}
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

/** "1胜 2半赢 3负 1半输 2走"——四分之一盘口半赢/半输(2026-08-16)只在实际
 * 出现时才加进这行,避免绝大多数场次(没有 half_win/half_loss)时把恒为 0
 * 的分类也挤进公开首页摘要;出现时必须可见,不能被吞掉(CLAUDE.md 战绩纪律)。 */
function recoResultBreakdownText(overview: RecoOverview): string {
  const parts = [`${overview.win_count}胜`];
  if (overview.half_win_count > 0) parts.push(`${overview.half_win_count}半赢`);
  parts.push(`${overview.lose_count}负`);
  if (overview.half_loss_count > 0) parts.push(`${overview.half_loss_count}半输`);
  parts.push(`${overview.push_count}走`);
  return parts.join(" ");
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
            <b className="num">{recoResultBreakdownText(overview)}</b>
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
