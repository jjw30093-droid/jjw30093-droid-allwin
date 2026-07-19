import { Suspense } from "react";
import Link from "next/link";
import {
  serverGet,
  serverGetOptional,
  type MatchListResponse,
  type PredictionResponse,
} from "@/lib/api-v1";
import { MatchRow, type FreeTip } from "@/components/matches/MatchRow";
import { LocalTime } from "@/components/matches/LocalTime";
import { entitlementLabel, formatPrice } from "@/components/matches/zh";
import type {
  ModelMetricsResponse,
  ProductsResponse,
} from "@/components/matches/types";
import styles from "./page.module.css";

/* ── 公共小件 ─────────────────────────────────────────────── */

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
  const p = resp.prediction;
  // 匿名请求只会拿到 free DTO;full 分支仅为类型完备
  if (p.tier === "free") {
    return { top_outcome: p.top_outcome, top_probability: p.top_probability };
  }
  return null;
}

/* ── 今日/近期比赛 ────────────────────────────────────────── */

async function RecentMatchesSection() {
  let upcoming: MatchListResponse;
  let finished: MatchListResponse;
  try {
    [upcoming, finished] = await Promise.all([
      serverGet<MatchListResponse>("/api/v1/matches?status=upcoming&limit=5", {
        revalidate: 60,
      }),
      serverGet<MatchListResponse>("/api/v1/matches?status=finished&limit=5", {
        revalidate: 60,
      }),
    ]);
  } catch {
    return <ErrorBox text="比赛数据暂时无法加载,请稍后刷新重试。" />;
  }

  const tips = await Promise.all(
    upcoming.matches.map((m) =>
      serverGetOptional<PredictionResponse>(
        `/api/v1/matches/${m.match_id}/prediction`,
      ).catch(() => null),
    ),
  );

  return (
    <div className={styles.twoCol}>
      <div className={styles.card}>
        <h3 className={styles.cardTitle}>近期赛程</h3>
        {upcoming.matches.length === 0 ? (
          <p className={styles.emptyText}>暂无已排期的未来比赛。</p>
        ) : (
          upcoming.matches.map((m, i) => (
            <MatchRow key={m.match_id} match={m} freeTip={freeTipOf(tips[i])} />
          ))
        )}
      </div>
      <div className={styles.card}>
        <h3 className={styles.cardTitle}>最近完赛</h3>
        {finished.matches.length === 0 ? (
          <p className={styles.emptyText}>暂无已完赛的比赛记录。</p>
        ) : (
          finished.matches.map((m) => <MatchRow key={m.match_id} match={m} />)
        )}
      </div>
      <p className={styles.sectionFoot}>
        免费层展示英超数据与模型最高一项概率;比赛日期为 UTC 口径的比赛日。
        <Link href="/matches" className={styles.moreLink}>
          全部比赛 →
        </Link>
      </p>
    </div>
  );
}

/* ── 正式模型表现 ─────────────────────────────────────────── */

async function MetricsSection() {
  let data: ModelMetricsResponse;
  try {
    data = await serverGet<ModelMetricsResponse>("/api/v1/model/metrics", {
      revalidate: 300,
    });
  } catch {
    return <ErrorBox text="模型指标暂时无法加载,请稍后刷新重试。" />;
  }

  const ev = data.official_evaluation;
  if (!ev) {
    return (
      <div className={styles.card}>
        <p className={styles.emptyText}>
          {data.official_evaluation_note ??
            "暂无符合口径的正式样本(正式样本 = 开球前发布并锁定的预测)。"}
        </p>
        <p className={styles.sectionFoot}>
          正式战绩只统计开球前发布并锁定、赛后不可修改的预测。
          <Link href="/track-record" className={styles.moreLink}>
            公开战绩 →
          </Link>
          <Link href="/about-model" className={styles.moreLink}>
            模型说明 →
          </Link>
        </p>
      </div>
    );
  }

  return (
    <div className={styles.card}>
      <div className={styles.statGrid}>
        <div className={styles.stat}>
          <span className={styles.statLabel}>正式样本量</span>
          <span className={`${styles.statValue} num`}>{ev.sample_size}</span>
        </div>
        <div className={styles.stat}>
          <span className={styles.statLabel}>命中率</span>
          <span className={`${styles.statValue} num`}>
            {ev.accuracy != null ? `${(ev.accuracy * 100).toFixed(1)}%` : "—"}
          </span>
        </div>
        <div className={styles.stat}>
          <span className={styles.statLabel}>Brier</span>
          <span className={`${styles.statValue} num`}>
            {ev.brier != null ? ev.brier.toFixed(4) : "—"}
          </span>
        </div>
        <div className={styles.stat}>
          <span className={styles.statLabel}>RPS</span>
          <span className={`${styles.statValue} num`}>
            {ev.rps != null ? ev.rps.toFixed(4) : "—"}
          </span>
        </div>
      </div>
      <p className={styles.sectionFoot}>
        评估时间:<LocalTime iso={ev.evaluated_at} fallback="未提供" />
        ;口径:开球前发布并锁定的正式预测,离线评估,不挑选样本。
        <Link href="/track-record" className={styles.moreLink}>
          逐场记录 →
        </Link>
      </p>
      <p className={styles.finePrint}>{data.market_baseline.note}</p>
    </div>
  );
}

/* ── 免费 vs 会员 ─────────────────────────────────────────── */

async function PlansSection() {
  let data: ProductsResponse;
  try {
    data = await serverGet<ProductsResponse>("/api/v1/products", {
      revalidate: 300,
    });
  } catch {
    return <ErrorBox text="套餐信息暂时无法加载,请稍后刷新重试。" />;
  }
  if (data.plans.length === 0) {
    return (
      <div className={styles.card}>
        <p className={styles.emptyText}>套餐信息暂未配置。</p>
      </div>
    );
  }
  const plans = [...data.plans].sort((a, b) => a.rank - b.rank);
  return (
    <div>
      <div className={styles.planGrid}>
        {plans.map((plan) => {
          const products = data.products
            .filter((p) => p.plan_id === plan.id)
            .sort((a, b) => a.sort_order - b.sort_order);
          return (
            <div key={plan.id} className={styles.planCard}>
              <h3 className={styles.planName}>{plan.name_zh}</h3>
              {plan.description && (
                <p className={styles.planDesc}>{plan.description}</p>
              )}
              <ul className={styles.entList}>
                {plan.entitlements.map((ent) => (
                  <li key={ent}>{entitlementLabel(ent)}</li>
                ))}
              </ul>
              {products.length > 0 ? (
                <div className={styles.priceList}>
                  {products.map((p) => (
                    <div key={p.id} className={styles.priceRow}>
                      <span>{p.name_zh}</span>
                      <span className={`${styles.price} num`}>
                        {formatPrice(p.price_cents, p.currency)}
                        <i className={styles.priceUnit}>
                          /{p.duration_days}天
                        </i>
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className={styles.freeTag}>免费,无需付费</p>
              )}
            </div>
          );
        })}
      </div>
      <p className={styles.sectionFoot}>
        价格与权益均来自后端配置,以
        <Link href="/pricing" className={styles.moreLink}>
          会员页 →
        </Link>
        为准。
      </p>
    </div>
  );
}

/* ── 页面 ─────────────────────────────────────────────────── */

export default function Home() {
  return (
    <main className={styles.page}>
      <section className={styles.hero}>
        <h1 className={styles.heroTitle}>开赛前,把一场比赛讲清楚</h1>
        <p className={styles.heroSub}>
          面向中文足球用户的数据分析平台:真实比赛数据、模型概率与不确定性、
          公开且不可篡改的预测记录——讲清楚依据,也讲清楚局限。
        </p>
        <div className={styles.heroActions}>
          <Link href="/matches" className={styles.ctaPrimary}>
            浏览比赛
          </Link>
          <Link href="/track-record" className={styles.ctaSecondary}>
            公开战绩
          </Link>
        </div>
      </section>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>今日 · 近期比赛</h2>
        <Suspense fallback={<SectionSkeleton lines={5} />}>
          <RecentMatchesSection />
        </Suspense>
      </section>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>正式模型表现</h2>
        <Suspense fallback={<SectionSkeleton lines={3} />}>
          <MetricsSection />
        </Suspense>
      </section>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>免费 vs 会员</h2>
        <Suspense fallback={<SectionSkeleton lines={4} />}>
          <PlansSection />
        </Suspense>
      </section>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>内容工作台 Studio</h2>
        <div className={styles.card}>
          <p className={styles.studioText}>
            同一份版本化分析数据,同时驱动网站比赛页与内容制作:
            六段式竖屏图卡、口播稿、字幕文件一键导出,
            每份导出都标注数据截止时间与模型版本。
          </p>
          <ul className={styles.studioList}>
            <li>竖屏图卡:1080×1920 / 1080×1350 PNG</li>
            <li>口播稿与字幕:纯文本 / JSON / SRT</li>
            <li>与比赛详情页同源:不存在两套相互矛盾的解释</li>
          </ul>
        </div>
      </section>

      <footer className={styles.disclaimer}>
        <p>
          本站全部内容为数据研究与内容创作素材,不构成任何投注建议;
          模型概率存在不确定性,历史表现不代表未来。
        </p>
        <p>
          各数据区块的更新时间以具体页面标注为准;正式预测一经锁定不可修改,
          撤回也保留原始记录,全部历史公开可查。
        </p>
      </footer>
    </main>
  );
}
