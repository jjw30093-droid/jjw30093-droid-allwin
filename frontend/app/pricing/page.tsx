import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { serverGet, type GetJson } from "@/lib/api-v1";
import styles from "./pricing.module.css";

export const metadata: Metadata = {
  title: "访问权限说明",
  description:
    "足球数据免费:登录即可查看全部联赛数据、胜平负概率与完整赔率时间线;赛前每日精选按场为账号开通授权。",
};

/* 类型从 OpenAPI 生成类型派生(Pydantic 单一真源,宪法 §10.3);
 * 展示哪些层级来自 DB 的 is_active 套餐,不在前端虚构层级。 */
type ProductsResponse = GetJson<"/api/v1/products">;
type PlanDTO = ProductsResponse["plans"][number];

/**
 * 三层权限的用户视角说明。2026-08-16 权限口径修正:除"每日精选"外,全站
 * 比赛内容对任何人(含匿名)完全一致,登录不解锁任何额外足球数据——登录
 * 只解锁收藏、精选授权状态查询等账户类个人功能。
 *
 * 2026-09-16 修正:这段注释和下面「注册用户」那一栏此前都写着"历史战绩
 * 查看"要登录,但历史战绩自 2026-08-16 起就是匿名全开的
 * (backend/api/routes_reco.py::reco_track_record)。口径改了、这两处文案
 * 没跟着改,结果是在告诉用户"战绩要登录才能看"——站长收到的"看不到战绩"
 * 那条真实反馈,根因之一就是这里。现已挪到「游客」一栏。
 * 技术上的 plan id / entitlement 键值属于内部实现,不向用户展示;未在此
 * 登记的新套餐回退展示 DB 中的 name_zh + description。
 */
const PLAN_PRESENTATION: Record<
  string,
  { title: string; how: string; lines: string[] }
> = {
  free: {
    title: "游客",
    how: "无需登录",
    lines: [
      "全部联赛的完整比赛资料与胜平负概率",
      "完整赔率时间线",
      "每日公推的全部内容",
      "每日精选与公推的历史战绩(不用登录)",
    ],
  },
  member: {
    // 当前没有自助注册:免费账号靠公众号申请开通,文案必须与实际一致(2026-09-26)
    title: "免费账号",
    how: "通过公众号申请开通，无需付费",
    lines: [
      "收藏关注的比赛",
      "查询本账号的每日精选授权状态",
    ],
  },
  daily_picks: {
    title: "精选授权用户",
    how: "按场为你的账号开通",
    lines: [
      "包含免费账号的全部内容",
      "已获授权那场比赛的赛前每日精选",
    ],
  },
};

function TierCard({ plan }: { plan: PlanDTO }) {
  const view = PLAN_PRESENTATION[plan.id];
  if (!view) {
    // 未登记的新套餐:回退 DB 文案,绝不展示内部权益键值
    return (
      <div className={styles.planCard}>
        <div className={styles.planName}>{plan.name_zh}</div>
        {plan.description && <p className={styles.planDesc}>{plan.description}</p>}
      </div>
    );
  }
  return (
    <div className={styles.planCard}>
      <div className={styles.planName}>{view.title}</div>
      <p className={styles.freeNote}>{view.how}</p>
      <ul className={styles.entList}>
        {view.lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </div>
  );
}

/* ── 数据区块(Suspense 内)────────────────────────────── */

// 导出仅供 tests/pricing-page.test.tsx 直接调用+渲染:Suspense 包裹的 async
// server component 在当前 vitest(jsdom + @testing-library/react)环境下不会
// 真正 resolve(一直停留在 fallback),必须绕开 Suspense 单独测试这个区块。
export async function AccessTiers() {
  let data: ProductsResponse;
  try {
    data = await serverGet<ProductsResponse>("/api/v1/products", { revalidate: 300 });
  } catch (err) {
    return (
      <div className={styles.errorBox}>
        <div className={styles.errorTitle}>权限说明暂时无法加载</div>
        <p>
          后端 API 未响应,请稍后重试。
          <br />
          <span className={styles.errorDetail}>
            {err instanceof Error ? err.message : String(err)}
          </span>
        </p>
      </div>
    );
  }

  const plans = [...data.plans].sort((a, b) => a.rank - b.rank);
  if (plans.length === 0) {
    return (
      <div className={styles.emptyCard}>
        <div className={styles.emptyTitle}>权限配置尚未就绪</div>
        <p className={styles.emptyText}>请稍后再来。</p>
      </div>
    );
  }

  return (
    <div className={styles.planGrid}>
      {plans.map((plan) => (
        <TierCard key={plan.id} plan={plan} />
      ))}
    </div>
  );
}

function Skeleton() {
  return (
    <div aria-hidden="true">
      <div className={styles.planGrid}>
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className={`${styles.planCard} ${styles.skeletonCard}`} />
        ))}
      </div>
    </div>
  );
}

/* ── 页面入口 ───────────────────────────────────────────── */

export default function PricingPage() {
  return (
    <main className={styles.page}>
      <div className={styles.header}>
        <h1 className={styles.title}>会员与权限</h1>
      </div>

      {/* 「如何获得精选授权」放最上面,写成三步(/reco 的「怎么开通」按钮跳到这里) */}
      <section id="how-to-unlock" className={styles.noticeCard} aria-labelledby="how-to-unlock-title">
        <h2 id="how-to-unlock-title" className={styles.stepsTitle}>
          如何获得精选授权
        </h2>
        <ol className={styles.steps}>
          <li>
            <span>
              <Link href="/login">登录账号</Link>
            </span>
          </li>
          <li>
            <span>通过公众号联系我们开通</span>
          </li>
          <li>
            <span>
              回到「<Link href="/reco">精选</Link>」页查看
            </span>
          </li>
        </ol>
      </section>

      <p className={styles.subtitle}>
        比赛数据、赔率、概率都不用登录，匿名打开就是完整的。登录只用来收藏、看历史战绩和管账号。只有赛前的<strong>每日精选</strong>，要按场给你的账号开通。
      </p>

      <Suspense fallback={<Skeleton />}>
        <AccessTiers />
      </Suspense>
    </main>
  );
}
