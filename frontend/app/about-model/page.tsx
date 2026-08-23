import type { Metadata } from "next";
import { Suspense } from "react";
import { serverGet, type GetJson } from "@/lib/api-v1";
import { LocalTime } from "@/components/trust/LocalTime";
import styles from "./about-model.module.css";

export const metadata: Metadata = {
  title: "模型说明",
  description:
    "预测模型的通俗说明：数据来源、特征、Dixon-Coles + isotonic 校准原理、walk-forward 评估方法、各项指标解释与已知局限。",
};

/* 类型从 OpenAPI 生成类型派生(Pydantic 单一真源,宪法 §10.3)。
 * params / dev_metrics 在契约里就是宽 dict,数字字段全部做运行时类型守卫,
 * API 缺数据的项不显示数字。 */
type ModelMetricsResponse = GetJson<"/api/v1/model/metrics">;
type ModelVersionDTO = ModelMetricsResponse["model_versions"][number];

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
          我们<strong>没拿模型跟收盘赔率正经比过，所以不说谁强</strong>。
          要比就得先把公司范围、去水算法、缺场怎么处理都定死，那套还没做。
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
        模型给的是概率，不是结果。下面说清楚它拿什么算、怎么算、准不准、哪儿不灵。
      </p>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>数据来源</h2>
        <p className={styles.prose}>
          比赛、球队和 xG 数据来自 FotMob 公开数据，覆盖我们已经接入的各个联赛，
          完整名单见「联赛数据」页面。模型训练只用真实踢完的比赛数据。
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
          先用两队近期的 xG 估出这场各自大概能进几个，再按泊松分布把每个比分的
          可能性算一遍，汇总成主胜、平局、客胜。
        </p>
        <p className={styles.prose}>
          光靠泊松算，0:0、1:1 这种小比分平局会偏少，跟实际对不上。Dixon-Coles
          就是专门补这块的，把平局概率拉回真实水平。
        </p>
        <p className={styles.prose}>
          最后拿历史数据核一遍：模型说 40% 的事，长期得真有四成发生。对不上就按
          胜、平、负分别往回拉，不是把概率往两头推。
        </p>
      </section>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>评估方法:walk-forward</h2>
        <p className={styles.prose}>
          回测只喂给模型开赛前就有的数据，让它预测下一场，再往前挪一格。这样它
          偷看不到未来，成绩才有参考价值。赛季刚开始样本少，数字会难看，我们照
          原样放着。
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
            <strong>刚开季的比赛准不准</strong>：每队才踢两三场，模型手里的样本不够，
            这时候的概率误差大，会标低置信度。
          </li>
          <li>
            <strong>升班马呢</strong>：上赛季不在这个级别，没有可比的数据，实力估得偏。
          </li>
          <li>
            <strong>参考盘口吗</strong>：算的时候完全不参考博彩公司的盘，所以它跟盘口
            不一致是正常的。
          </li>
          <li>
            <strong>看首发和伤停了吗</strong>：详情页那些预计首发、伤停名单是给你看的，
            模型算概率时没用上。大轮换的场次概率会偏。
          </li>
          <li>
            <strong>红牌暴雨这种意外呢</strong>：红牌、暴雨、门将送礼，这些没法预测。
            概率说的是长期频率。
          </li>
          <li>
            <strong>60% 是不是保证</strong>：主胜 60%，意思是这种局面长期看有四成不赢。
            一场的输赢证明不了模型准不准。
          </li>
        </ul>
      </section>
    </main>
  );
}
