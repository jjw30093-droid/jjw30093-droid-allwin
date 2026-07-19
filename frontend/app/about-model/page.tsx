import type { Metadata } from "next";
import { Suspense } from "react";
import { serverGet } from "@/lib/api-v1";
import { LocalTime } from "@/components/trust/LocalTime";
import styles from "./about-model.module.css";

export const metadata: Metadata = {
  title: "模型说明 — 欧赢 allwin",
  description:
    "预测模型的通俗说明:数据来源、特征、Dixon-Coles + isotonic 校准原理、walk-forward 评估方法、各项指标解释与已知局限。",
};

/* /api/v1/model/metrics 无 response_model,OpenAPI 生成类型为 unknown;
 * 此接口结构以 backend/api/routes_public.py::model_metrics 为准,在此按实际
 * 返回结构收窄。数字字段全部做运行时类型守卫,API 缺数据的项不显示数字。 */
interface ModelVersionDTO {
  id: string;
  algorithm: string;
  description: string;
  trained_at: string | null;
  train_range: string | null;
  created_at: string;
  params: Record<string, unknown>;
  dev_metrics: Record<string, unknown>;
}

interface OfficialEvaluationDTO {
  sample_size: number;
  accuracy: number | null;
  brier: number | null;
  log_loss: number | null;
  rps: number | null;
  calibration: unknown[];
  evaluated_at: string | null;
}

interface ModelMetricsResponse {
  model_versions: ModelVersionDTO[];
  official_evaluation: OfficialEvaluationDTO | null;
  official_evaluation_note: string | null;
  market_baseline: { status: string; note: string };
}

function asNum(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function asStr(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}

function asStrList(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

/* ── 模型版本卡(数字全部来自 API)──────────────────────── */

function VersionCard({ v }: { v: ModelVersionDTO }) {
  const rps = asNum(v.dev_metrics["rps"]);
  const baselineRps = asNum(v.dev_metrics["baseline_rps"]);
  const baselineName = asStr(v.dev_metrics["baseline"]);
  const testSeason = asStr(v.dev_metrics["test_season"]);
  const note = asStr(v.dev_metrics["note"]);
  const rho = asNum(v.params["rho"]);
  const features = asStrList(v.params["features"]);
  const nGoals = asNum(v.params["n_goals"]);

  return (
    <div className={styles.versionCard}>
      <div className={styles.versionHead}>
        <span className={styles.versionId}>{v.id}</span>
        <span className={styles.versionAlgo}>{v.algorithm}</span>
      </div>
      {v.description && <p className={styles.versionDesc}>{v.description}</p>}
      <dl className={styles.kvList}>
        {v.train_range && (
          <>
            <dt>训练区间</dt>
            <dd className="num">{v.train_range}</dd>
          </>
        )}
        {testSeason && (
          <>
            <dt>测试赛季</dt>
            <dd className="num">{testSeason}</dd>
          </>
        )}
        {features.length > 0 && (
          <>
            <dt>输入特征</dt>
            <dd className="num">{features.join(" · ")}</dd>
          </>
        )}
        {nGoals != null && (
          <>
            <dt>滚动窗口</dt>
            <dd>
              近 <span className="num">{nGoals}</span> 场
            </dd>
          </>
        )}
        {rho != null && (
          <>
            <dt>DC 修正参数 ρ</dt>
            <dd className="num">{rho}</dd>
          </>
        )}
        {rps != null && (
          <>
            <dt>测试集 RPS</dt>
            <dd>
              <span className={`num ${styles.goldNum}`}>{rps.toFixed(4)}</span>
              {baselineRps != null && (
                <span className={styles.baselineNote}>
                  (对照:{baselineName ?? "基线"}{" "}
                  <span className="num">{baselineRps.toFixed(4)}</span>,RPS 越低越好)
                </span>
              )}
            </dd>
          </>
        )}
        <dt>版本登记时间</dt>
        <dd>
          <LocalTime utc={v.created_at} />
        </dd>
      </dl>
      {note && <p className={styles.versionNote}>{note}</p>}
    </div>
  );
}

/* ── 数据区块(Suspense 内)────────────────────────────── */

async function AboutModelContent() {
  let data: ModelMetricsResponse;
  try {
    data = await serverGet<ModelMetricsResponse>("/api/v1/model/metrics", { revalidate: 300 });
  } catch (err) {
    return (
      <div className={styles.errorBox}>
        <div className={styles.errorTitle}>模型指标暂时无法加载</div>
        <p>
          后端 API 未响应,以下原理说明仍然有效,数字指标请稍后重试。
          <br />
          <span className={styles.errorDetail}>
            {err instanceof Error ? err.message : String(err)}
          </span>
        </p>
      </div>
    );
  }

  const ev = data.official_evaluation;

  return (
    <>
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>模型版本与回测表现</h2>
        {data.model_versions.length > 0 ? (
          data.model_versions.map((v) => <VersionCard key={v.id} v={v} />)
        ) : (
          <p className={styles.emptyText}>暂无已登记的模型版本。</p>
        )}
        <p className={styles.footnote}>
          上表回测指标来自研发期 walk-forward 评估,对照为历史主/平/客频率基线,
          <strong>不是</strong>博彩公司收盘赔率共识。
        </p>
      </section>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>正式样本评估</h2>
        {ev ? (
          <>
            <div className={styles.officialGrid}>
              <div className={styles.officialItem}>
                <span className={styles.officialLabel}>样本量</span>
                <span className={`num ${styles.officialValue}`}>{ev.sample_size}</span>
              </div>
              {ev.accuracy != null && (
                <div className={styles.officialItem}>
                  <span className={styles.officialLabel}>Accuracy</span>
                  <span className={`num ${styles.officialValue}`}>
                    {(ev.accuracy * 100).toFixed(1)}%
                  </span>
                </div>
              )}
              {ev.brier != null && (
                <div className={styles.officialItem}>
                  <span className={styles.officialLabel}>Brier</span>
                  <span className={`num ${styles.officialValue}`}>{ev.brier.toFixed(4)}</span>
                </div>
              )}
              {ev.log_loss != null && (
                <div className={styles.officialItem}>
                  <span className={styles.officialLabel}>Log Loss</span>
                  <span className={`num ${styles.officialValue}`}>{ev.log_loss.toFixed(4)}</span>
                </div>
              )}
              {ev.rps != null && (
                <div className={styles.officialItem}>
                  <span className={styles.officialLabel}>RPS</span>
                  <span className={`num ${styles.officialValue}`}>{ev.rps.toFixed(4)}</span>
                </div>
              )}
            </div>
            {ev.evaluated_at && (
              <p className={styles.footnote}>
                评估时间 <LocalTime utc={ev.evaluated_at} />;明细见公开战绩页。
              </p>
            )}
          </>
        ) : (
          <p className={styles.emptyText}>
            {data.official_evaluation_note ??
              "暂无正式样本评估;正式预测流程启动并完赛结算后,这里展示离线评估结果。"}
          </p>
        )}
      </section>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>与市场的关系</h2>
        <p className={styles.prose}>
          我们<strong>未与博彩公司收盘赔率做过可复现的配对对比,不声称模型优于市场</strong>。
          完成收盘赔率评估需要固定公司集合、去水公式、缺失规则与配对样本,做到之前不做任何比较声明。
        </p>
        <p className={styles.unverified}>
          <span className={styles.unverifiedBadge}>{data.market_baseline.status}</span>
          {data.market_baseline.note}
        </p>
      </section>
    </>
  );
}

function Skeleton() {
  return (
    <div aria-hidden="true">
      <div className={styles.skeletonBlock} />
      <div className={styles.skeletonBlock} />
    </div>
  );
}

/* ── 页面入口 ───────────────────────────────────────────── */

export default function AboutModelPage() {
  return (
    <main className={styles.page}>
      <div className={styles.header}>
        <h1 className={styles.title}>模型说明</h1>
      </div>
      <p className={styles.subtitle}>
        模型输出的是概率与不确定性,不是确定的比赛结果。这一页把它的原料、原理、评估方法和局限讲清楚。
      </p>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>数据来源</h2>
        <p className={styles.prose}>
          比赛、球队与 xG 数据来自 FotMob 公开数据,覆盖五大联赛,按
          Bronze(原始落库)→ Silver(清洗整理)→ Gold(模型产物)分层管理。
          模型训练只使用真实完赛数据;各来源的验证状态与时间粒度以数据源文档为准。
        </p>
      </section>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>特征:滚动 xG 窗口</h2>
        <p className={styles.prose}>
          模型不看球队名气,只看近期的攻防质量:对每支球队取最近若干场比赛的
          xG(预期进球)与被对手创造的 xG,滚动更新。xG 衡量的是机会质量而非运气比分,
          比“近期赢了几场”更稳定。窗口长度等具体参数见上方模型版本卡,全部来自后端登记信息。
        </p>
      </section>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>原理:Dixon-Coles + isotonic 校准</h2>
        <p className={styles.prose}>
          第一步,用滚动 xG 估计两队本场的预期进球数(λ),假设进球数服从泊松分布,
          由此算出每个比分的概率,再汇总成主胜/平局/客胜三项概率。
        </p>
        <p className={styles.prose}>
          第二步,Dixon-Coles 修正:纯泊松假设会低估 0-0、1-1 这类低比分平局的出现频率,
          DC 项(参数 ρ)专门修正低比分区域的联合概率,让平局概率更接近真实。
        </p>
        <p className={styles.prose}>
          第三步,isotonic(保序)校准:把模型的原始概率对照历史实际频率做单调映射,
          按胜/平/负三类分别校准——目标是“模型说 40% 的事件,长期看就发生约 40%”,
          而不是把概率往极端推。
        </p>
      </section>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>评估方法:walk-forward</h2>
        <p className={styles.prose}>
          评估只用“比赛发生之前已知的数据”训练,预测其后的比赛,然后滚动前进。
          这样模型永远不会偷看未来数据,回测成绩更接近真实使用场景;
          代价是赛季初样本少时表现天然更不稳定,我们如实展示而不是掩盖。
        </p>
      </section>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>指标怎么读</h2>
        <dl className={styles.metricDl}>
          <dt>Accuracy(命中率)</dt>
          <dd>
            概率最高一项与实际结果一致的比例。直观但粗糙:它不奖励“把握大小说得准”,只看对错。越高越好。
          </dd>
          <dt>Brier</dt>
          <dd>
            三项概率与实际结果(0/1)的均方误差。既罚说错,也罚“说对了但概率给得离谱”。越低越好。
          </dd>
          <dt>Log Loss(对数损失)</dt>
          <dd>
            对“把实际发生的结果给了极低概率”惩罚极重的指标,逼模型诚实报告不确定性。越低越好。
          </dd>
          <dt>RPS(排序概率得分)</dt>
          <dd>
            考虑“胜-平-负”是有顺序的:把主胜猜成平局,比猜成客胜错得轻。足球胜平负预测的常用主指标。越低越好。
          </dd>
          <dt>校准(Calibration)</dt>
          <dd>
            把预测按概率分桶,对比每桶的实际发生频率。校准好 = 模型说 60% 的比赛长期确实赢约 60%。
          </dd>
        </dl>
      </section>

      <Suspense fallback={<Skeleton />}>
        <AboutModelContent />
      </Suspense>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>已知局限</h2>
        <ul className={styles.limitList}>
          <li>
            <strong>冷启动</strong>:赛季初每队样本少,滚动 xG 窗口未填满,概率不确定性明显更大;
            这类预测会标注低置信度。
          </li>
          <li>
            <strong>升班马</strong>:缺乏顶级联赛历史数据,模型对其实力估计误差更大。
          </li>
          <li>
            <strong>不含赔率特征</strong>:当前模型不使用博彩公司赔率作为输入,也未与收盘赔率对比。
          </li>
          <li>
            <strong>不含阵容与伤停</strong>:临场首发、伤停、停赛等信息尚未进入特征,重大轮换会使概率偏差。
          </li>
          <li>
            <strong>低频事件</strong>:红牌、极端天气、门将失误等随机事件无法预测,概率只描述长期频率。
          </li>
          <li>
            <strong>概率不是承诺</strong>:60% 的主胜意味着约 4 成情况下主队不赢;单场结果不能证明或证伪模型。
          </li>
        </ul>
      </section>
    </main>
  );
}
