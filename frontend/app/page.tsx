import { cache, Suspense } from "react";
import Image from "next/image";
import Link from "next/link";
import {
  serverGet,
  serverGetOptional,
  type GetJson,
  type MatchListResponse,
} from "@/lib/api-v1";
import { selectFeaturedMatch, type HomeMatchCard } from "@/lib/homepage";
import { LocalTime } from "@/components/matches/LocalTime";
import { ContinueWatching } from "@/components/home/ContinueWatching";
import { HomeMatchExperienceLive, FreshnessBlock } from "@/components/home/HomeMatchExperienceLive";
import styles from "./page.module.css";

type RecoOverview = GetJson<"/api/v1/reco/overview">;
type Freshness = GetJson<"/api/v1/status/freshness">;

function SectionSkeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className={styles.skeleton} aria-hidden>
      <Image
        src="/brand/logo-mark.png"
        alt=""
        width={88}
        height={88}
        className={styles.skeletonMascot}
      />
      <div className={styles.skeletonLines}>
        {Array.from({ length: lines }, (_, i) => (
          <span key={i} className={styles.skelLine} />
        ))}
        <span className={styles.skeletonHint}>喵弟正在翻数据…</span>
      </div>
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
 * 数据更新条(freshness)一并在这里取——公开只读、不受身份影响,SSR 一次
 * 即可,不需要跟着 HomeMatchExperienceLive 的客户端 cookie 刷新重新拉。
 * 2026-08-23 首页信息架构重排:渲染挪到了页面底部(见 FreshnessSection),
 * 不再放在首屏的计数条里,但数据获取仍然收在这同一个聚合函数内——
 * getFreshness() 是 React cache(),FreshnessSection 会拿到同一份结果,
 * 不是发起第二次网络请求。
 */
export const getHomePageData = cache(async (): Promise<HomePageData> => {
  const [upcoming, todayList, tomorrowList, shotsList, freshness] = await Promise.all([
    // boost=free_predicted(2026-08-16):limit 截断的是原始 API 顺序,
    // 完整 7 天窗口里更靠后的比赛没机会进这一页——哪怕它才是唯一一场
    // "免费且已发布概率"的比赛(见 backend/api/routes_public.py::list_matches
    // 同名参数文档)。opt-in 参数把这场比赛在服务端顶进 limit 截断线以内,
    // 不需要把整窗口 ~95 场完整 MatchSummary 都下发到这里再筛。
    //
    // limit=70(2026-08-19,原为 8):重点位改成"24 小时内 + 强强对话优先"
    // (lib/homepage.ts::selectHomepageMatches)后,8 场候选结构性不够用——
    // 一场晚间的 Big6 内战完全可能排在第 15 位而根本进不了池。
    //
    // 70 这个数字对齐的是**滚动 24 小时窗口**的容量,不是"峰值日场次":门槛
    // 是 (now, now+24h] 这个滑动窗口,会横跨两个自然日。对生产 3798 场未来
    // 赛程实测,最密集的滚动 24h 窗口有 70 场(起点 2026-11-21T00:00Z),
    // 按自然日峰值(49 场)取 50 会让其中约 20 场结构性地进不了候选池。
    // 上限是后端的 Query(le=200),70 仍有余量。
    // 必须与 HomeMatchExperienceLive.tsx::fetchHomeData 保持同一个 limit:
    // 两处用的是同一个纯函数判据,候选池不一样会让挂载后的刷新换掉重点卡。
    serverGet<MatchListResponse>(
      "/api/v1/matches?status=upcoming&window=7d&limit=70&boost=free_predicted",
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
        initialErrored
      />
    );
  }

  return (
    <HomeMatchExperienceLive
      initialFeatured={data.featured}
      initialSecondary={data.secondary}
      initialCounts={data.counts}
      initialErrored={false}
    />
  );
}

/* ── 数据更新状态(2026-08-23 从首屏计数条挪到页面底部) ──────────
 * getFreshness() 是同一个 React cache()、同一次请求内已经被 getHomePageData()
 * 调过一次——这里不是第二次网络请求,是同一份缓存结果的第二次读取。 */
async function FreshnessSection() {
  const freshness = await getFreshness();
  return <FreshnessBlock freshness={freshness} />;
}

/* ── 今日精选(2026-08-23 合并):发布状态 + 近 N 天战绩摘要 ─────────
 * 这两块此前分开渲染,却读的是同一个 getRecoOverview() 数据源,首页因此
 * 出现四个指向 /reco 的入口。合并成一张卡,只留"查看今日精选"一个主 CTA。 */

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

async function DailyPicksSection() {
  const overview = await getRecoOverview();
  const hasRecords = !!overview && overview.settled_count > 0;
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
        <p className={styles.picksNote}>精选状态暂时加载不出来，可以直接进精选页看看。</p>
      ) : overview.today_published_count > 0 ? (
        <p className={styles.picksNote}>
          今天已发布 <b className="num">{overview.today_published_count}</b> 场
          {overview.today_latest_published_at && (
            <>
              ，更新于 <LocalTime iso={overview.today_latest_published_at} />
            </>
          )}
          。内容包含赛果方向、数据依据与风险提示。
        </p>
      ) : (
        <p className={styles.picksNote}>今天还没发。发了这儿会自己更新。</p>
      )}

      {overview &&
        (hasRecords ? (
          <div className={styles.recoSummaryRow}>
            <div className={styles.recoSummaryItem}>
              <b className="num">{overview.settled_count}</b>
              <span>近 {overview.window_days} 天已结算</span>
            </div>
            <div className={styles.recoSummaryItem}>
              <b className="num">{recoResultBreakdownText(overview)}</b>
              <span>命中/未中/走水</span>
            </div>
            <div className={styles.recoSummaryItem}>
              <b className={`num${overview.net_units > 0 ? ` ${styles.recoNetPositive}` : ""}`}>
                {overview.net_units >= 0 ? "+" : ""}
                {overview.net_units.toFixed(2)}
              </b>
              <span>净单位</span>
            </div>
            {overview.voided_count > 0 && (
              <div className={styles.recoSummaryItem}>
                <b className="num">{overview.voided_count}</b>
                <span>作废</span>
              </div>
            )}
          </div>
        ) : (
          <p className={styles.emptyText}>
            还没开始发推荐。第一单结算之后这里开始累计，中没中都留着。
          </p>
        ))}

      <div className={styles.picksActions}>
        <Link href="/reco?tab=daily" className={styles.picksCta}>
          查看今日精选
          <span aria-hidden>→</span>
        </Link>
      </div>
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

      <ContinueWatching />

      <nav className={styles.quickLinks} aria-label="常用入口">
        <Link href="/leagues">
          <strong>联赛数据</strong>
          <span>排名、赛程与球队统计</span>
        </Link>
        <Link href="/about-model">
          <strong>模型说明</strong>
          <span>方法论与校准结果</span>
        </Link>
        <Link href="/track-record">
          <strong>模型公开记录</strong>
          <span>查看发布与赛后评估</span>
        </Link>
        <Link href="/pricing">
          <strong>权限说明</strong>
          <span>比赛数据不用登录，精选要开通</span>
        </Link>
        <Link href="/about">
          <strong>关于我们</strong>
          <span>平台说明与合作</span>
        </Link>
      </nav>

      {/* 数据更新状态:2026-08-23 从首屏计数条挪到这里,紧邻页脚之前,
          不再占首屏空间。 */}
      <Suspense fallback={null}>
        <FreshnessSection />
      </Suspense>
    </main>
  );
}
