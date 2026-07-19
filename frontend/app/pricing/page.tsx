import type { Metadata } from "next";
import { Suspense } from "react";
import { serverGet } from "@/lib/api-v1";
import { RedeemBox } from "@/components/trust/RedeemBox";
import styles from "./pricing.module.css";

export const metadata: Metadata = {
  title: "会员 — 欧赢 allwin",
  description:
    "免费 / Pro / Premium 套餐权益对比与价格。当前未接线上支付,通过兑换码或管理员开通。",
};

/* /api/v1/products 无 response_model,OpenAPI 生成类型为 unknown;结构以
 * backend/api/routes_public.py::list_products 为准(plans + products 全部来自 DB)。 */
interface PlanDTO {
  id: string;
  name_zh: string;
  description: string;
  rank: number;
  entitlements: string[];
}

interface ProductDTO {
  id: string;
  plan_id: string;
  name_zh: string;
  description: string;
  duration_days: number;
  price_cents: number;
  currency: string;
  sort_order: number;
}

interface ProductsResponse {
  plans: PlanDTO[];
  products: ProductDTO[];
}

/* entitlement 中文化;未知 key 原样展示,不猜 */
const ENTITLEMENT_ZH: Record<string, string> = {
  "league:epl": "英超数据",
  "league:top5": "五大联赛数据",
  "prediction:top_probability": "每场最高一项概率",
  "prediction:full_wdl": "完整胜平负概率",
  "prediction:score_matrix": "比分概率矩阵",
  "report:deep": "深度分析报告",
  "odds:summary_delayed": "赔率概要(延迟)",
  "odds:history_full": "完整赔率历史",
  "odds:raw": "赔率原始快照",
  "export:basic": "基础导出",
  "export:full": "完整导出",
  "alert:odds": "赔率变化提醒",
};

function entitlementLabel(key: string): string {
  return ENTITLEMENT_ZH[key] ?? key;
}

function formatPrice(cents: number, currency: string): string {
  const amount = cents / 100;
  const text = Number.isInteger(amount) ? String(amount) : amount.toFixed(2);
  return currency === "CNY" ? `¥${text}` : `${text} ${currency}`;
}

/* ── 套餐卡 ─────────────────────────────────────────────── */

function PlanCard({ plan, products }: { plan: PlanDTO; products: ProductDTO[] }) {
  return (
    <div className={styles.planCard}>
      <div className={styles.planName}>{plan.name_zh}</div>
      {plan.description && <p className={styles.planDesc}>{plan.description}</p>}
      {products.length > 0 ? (
        <ul className={styles.productList}>
          {products.map((p) => (
            <li key={p.id} className={styles.productRow}>
              <span className={styles.productName}>{p.name_zh}</span>
              <span className={styles.productPrice}>
                <span className={`num ${styles.priceNum}`}>
                  {formatPrice(p.price_cents, p.currency)}
                </span>
                <span className={styles.productDuration}>
                  / <span className="num">{p.duration_days}</span> 天
                </span>
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className={styles.freeNote}>无需付费,注册即用</p>
      )}
      <ul className={styles.entList}>
        {plan.entitlements.map((e) => (
          <li key={e}>{entitlementLabel(e)}</li>
        ))}
      </ul>
    </div>
  );
}

/* ── 权益对比表 ─────────────────────────────────────────── */

function CompareTable({ plans }: { plans: PlanDTO[] }) {
  // 行序:按套餐从低到高首次出现的顺序,稳定去重
  const rows: string[] = [];
  for (const plan of plans) {
    for (const e of plan.entitlements) {
      if (!rows.includes(e)) rows.push(e);
    }
  }
  if (rows.length === 0) return null;
  return (
    <div className={styles.tableCard}>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th className={styles.thLeft}>权益</th>
              {plans.map((p) => (
                <th key={p.id}>{p.name_zh}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e}>
                <td className={styles.entName}>{entitlementLabel(e)}</td>
                {plans.map((p) => (
                  <td key={p.id} className={styles.checkCell}>
                    {p.entitlements.includes(e) ? (
                      <span className={styles.checkYes} aria-label="包含">
                        ✓
                      </span>
                    ) : (
                      <span className={styles.checkNo} aria-label="不包含">
                        –
                      </span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ── 数据区块(Suspense 内)────────────────────────────── */

async function PricingContent() {
  let data: ProductsResponse;
  try {
    data = await serverGet<ProductsResponse>("/api/v1/products", { revalidate: 300 });
  } catch (err) {
    return (
      <div className={styles.errorBox}>
        <div className={styles.errorTitle}>套餐信息暂时无法加载</div>
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
        <div className={styles.emptyTitle}>暂无可用套餐</div>
        <p className={styles.emptyText}>套餐配置尚未就绪,请稍后再来。</p>
      </div>
    );
  }
  const productsByPlan = (planId: string) =>
    data.products
      .filter((p) => p.plan_id === planId)
      .sort((a, b) => a.sort_order - b.sort_order);

  return (
    <>
      <div className={styles.planGrid}>
        {plans.map((plan) => (
          <PlanCard key={plan.id} plan={plan} products={productsByPlan(plan.id)} />
        ))}
      </div>

      <h2 className={styles.sectionTitle}>权益对比</h2>
      <CompareTable plans={plans} />
    </>
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
      <div className={styles.skeletonTable} />
    </div>
  );
}

/* ── 页面入口 ───────────────────────────────────────────── */

export default function PricingPage() {
  return (
    <main className={styles.page}>
      <div className={styles.header}>
        <h1 className={styles.title}>会员套餐</h1>
      </div>
      <p className={styles.subtitle}>
        所有套餐提供的都是数据、概率与分析内容,不提供投注建议;模型输出是概率,不是确定结果。
      </p>

      <div className={styles.noticeCard}>
        当前未接入线上支付:会员通过<strong>兑换码</strong>或<strong>管理员开通</strong>,
        价格与权益均来自后台配置,以下为实时读取结果。
      </div>

      <Suspense fallback={<Skeleton />}>
        <PricingContent />
      </Suspense>

      <h2 className={styles.sectionTitle}>兑换</h2>
      <RedeemBox />
    </main>
  );
}
