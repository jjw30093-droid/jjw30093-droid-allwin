import type { Metadata } from "next";
import { Suspense } from "react";
import { serverGet, type GetJson } from "@/lib/api-v1";
import { RedeemBox } from "@/components/trust/RedeemBox";
import styles from "./pricing.module.css";

export const metadata: Metadata = {
  title: "访问权限说明 — 欧赢 ALLWIN",
  description:
    "足球数据免费:登录即可查看全部联赛数据、模型完整概率与完整赔率时间线;赛前每日精选由站长为账号开通授权。",
};

/* 类型从 OpenAPI 生成类型派生(Pydantic 单一真源,宪法 §10.3);
 * 展示哪些层级来自 DB 的 is_active 套餐,不在前端虚构层级。 */
type ProductsResponse = GetJson<"/api/v1/products">;
type PlanDTO = ProductsResponse["plans"][number];

/**
 * 三层权限的用户视角说明。技术上的 plan id / entitlement 键值属于内部实现,
 * 不向用户展示;未在此登记的新套餐回退展示 DB 中的 name_zh + description。
 */
const PLAN_PRESENTATION: Record<
  string,
  { title: string; how: string; lines: string[] }
> = {
  free: {
    title: "游客",
    how: "无需登录",
    lines: [
      "浏览部分公开联赛的比赛资料",
      "每场比赛的最高一项模型概率",
      "延迟赔率概要",
    ],
  },
  member: {
    title: "注册用户",
    how: "免费登录即可,无需付费",
    lines: [
      "全部联赛的完整足球数据",
      "完整胜平负三项概率与比分矩阵",
      "赔率时间轴与变化记录",
      "每日精选的全部历史战绩",
    ],
  },
  daily_picks: {
    title: "精选授权用户",
    how: "由站长为账号开通",
    lines: [
      "包含注册用户的全部内容",
      "赛前每日精选(未结算推荐)",
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

async function AccessTiers() {
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
        <h1 className={styles.title}>访问权限说明</h1>
      </div>
      <p className={styles.subtitle}>
        登录不是付费:免费登录后即可查看全部联赛数据、模型完整概率、完整赔率时间线和历史战绩
        (比赛卡片上按赔率折算的胜平负概率条,匿名即可查看,登录后看到的赔率更完整、更及时)。
        只有<strong>赛前每日精选</strong>需要站长为账号单独开通授权。
        本站提供的都是数据、概率与分析内容,不提供投注建议;模型输出是概率,不是确定结果。
      </p>

      <Suspense fallback={<Skeleton />}>
        <AccessTiers />
      </Suspense>

      <h2 className={styles.sectionTitle}>如何获得精选授权</h2>
      <div className={styles.noticeCard}>
        当前未接入线上支付:通过<strong>公众号联系站长</strong>为账号开通,
        或在下方使用<strong>兑换码</strong>。授权生效后「每日精选」页会自动显示内容,
        无需重新注册。
      </div>
      <RedeemBox />
    </main>
  );
}
