import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { serverGet, type TrackRecordResponse } from "@/lib/api-v1";
import { buildMatchHref } from "@/lib/match-links";
import { LocalTime } from "@/components/trust/LocalTime";
import { formatPct } from "@/lib/format";
import styles from "./track-record.module.css";

export const metadata: Metadata = {
  title: "公开战绩",
  description:
    "全部正式预测的公开记录：样本量、Accuracy、Brier、Log Loss、RPS，逐场列出预测概率和实际比分，中了没中都在里面。",
};

const PAGE_SIZE = 50;

const OUTCOME_ZH: Record<string, string> = {
  home: "主胜",
  draw: "平局",
  away: "客胜",
};

export type Sample = TrackRecordResponse["samples"][number];

function fmt4(v: number | null | undefined): string | null {
  return typeof v === "number" && Number.isFinite(v) ? v.toFixed(4) : null;
}

/* ── 顶部指标卡 ─────────────────────────────────────────── */

function MetricCard({ label, value, unit }: { label: string; value: string; unit?: string }) {
  return (
    <div className={styles.metricCard}>
      <div className={styles.metricLabel}>{label}</div>
      <div className={styles.metricValue}>
        {value}
        {unit && <span className={styles.metricUnit}>{unit}</span>}
      </div>
    </div>
  );
}

function MetricsRow({ data }: { data: TrackRecordResponse }) {
  const m = data.metrics ?? null;
  const acc = m && typeof m.accuracy === "number" ? formatPct(m.accuracy, 1) : null;
  return (
    <>
      <div className={styles.metricsGrid}>
        <MetricCard label="正式样本" value={String(data.total)} unit="场" />
        <MetricCard label="Accuracy 命中率" value={acc ?? "—"} />
        <MetricCard label="Brier" value={fmt4(m?.brier) ?? "—"} />
        <MetricCard label="Log Loss" value={fmt4(m?.log_loss) ?? "—"} />
        <MetricCard label="RPS" value={fmt4(m?.rps) ?? "—"} />
      </div>
      {m ? (
        <p className={styles.metaLine}>
          离线评估样本量 <span className="num">{m.sample_size}</span> 场
          {m.evaluated_at && (
            <>
              {" "}
              · 评估时间 <LocalTime utc={m.evaluated_at} />
            </>
          )}
          {data.retracted_count > 0 && (
            <>
              {" "}
              · 含已撤回 <span className="num">{data.retracted_count}</span> 条（保留展示，不算进评估）
            </>
          )}
        </p>
      ) : (
        <p className={styles.metaLine}>还没算出指标。等这批比赛结算完就会出来。</p>
      )}
    </>
  );
}

/* ── 评估口径说明 ───────────────────────────────────────── */

function CriteriaCard() {
  return (
    <section className={styles.criteriaCard}>
      <h2 className={styles.criteriaTitle}>评估口径</h2>
      <ul className={styles.criteriaList}>
        <li>只算开球前就发出来、并且锁死不能改的预测。</li>
        <li>全部按开球时间排，没有日期筛选，也不挑。</li>
        <li>锁定之后还能改，但每次改都会记一笔——改了几次、最后一次什么时候，表格里都看得到。改完的数字会算进指标。</li>
        <li>撤回的预测还在表里，会标出来，不删。</li>
        <li>早期试跑的那批预测没法证明是开球前生成的，单独存着，不算进这张表。</li>
      </ul>
    </section>
  );
}

/* ── 记录表格 ───────────────────────────────────────────── */

function ProbCell({ value, isTop }: { value: number; isTop: boolean }) {
  return (
    <td className={`${styles.num} ${isTop ? styles.probTop : styles.probCell}`}>
      {formatPct(value, 1)}
    </td>
  );
}

export function SampleRow({ s }: { s: Sample }) {
  const retracted = s.status === "retracted";
  const hasScore = s.home_goals != null && s.away_goals != null;
  return (
    <tr className={retracted ? styles.retractedRow : undefined}>
      <td className={styles.timeCell}>
        <LocalTime utc={s.kickoff_at_utc} />
      </td>
      <td className={styles.matchCell}>
        {/* 链接放 <td> 内(表格行不能整行包 <a>);returnTo 让详情页渲染
            「返回公开战绩」(审计 B5:此前战绩行是死路) */}
        <Link
          href={buildMatchHref(s.match_id, "/track-record")}
          className={styles.matchLink}
        >
          <span className={styles.teamName}>{s.home.name}</span>
          <span className={styles.vs}>vs</span>
          <span className={styles.teamName}>{s.away.name}</span>
        </Link>
      </td>
      <ProbCell value={s.home_probability} isTop={s.predicted_outcome === "home"} />
      <ProbCell value={s.draw_probability} isTop={s.predicted_outcome === "draw"} />
      <ProbCell value={s.away_probability} isTop={s.predicted_outcome === "away"} />
      <td className={styles.predCell}>{OUTCOME_ZH[s.predicted_outcome]}</td>
      <td className={styles.resultCell}>
        {hasScore ? (
          <>
            <span className={`num ${styles.score}`}>
              {s.home_goals}-{s.away_goals}
            </span>
            {s.actual_outcome && (
              <span className={styles.actualOutcome}>{OUTCOME_ZH[s.actual_outcome]}</span>
            )}
          </>
        ) : (
          <span className={styles.pendingText}>未完赛</span>
        )}
      </td>
      <td>
        {s.hit === true && <span className={styles.hitYes}>命中</span>}
        {s.hit === false && <span className={styles.hitNo}>未中</span>}
        {s.hit == null && <span className={styles.pendingText}>待结算</span>}
      </td>
      <td className={styles.statusCell}>
        {retracted ? (
          <span className={styles.retractedBadge}>已撤回</span>
        ) : (
          <span className={styles.lockedBadge}>已锁定</span>
        )}
        {s.edit_count > 0 && (
          <span className={styles.editedBadge}>
            已修正 {s.edit_count} 次
            {s.last_edited_at && (
              <>
                {" · "}
                <LocalTime utc={s.last_edited_at} />
              </>
            )}
          </span>
        )}
      </td>
    </tr>
  );
}

function Pager({ page, total }: { page: number; total: number }) {
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if (totalPages <= 1) return null;
  return (
    <nav className={styles.pager} aria-label="分页">
      {page > 1 ? (
        <Link className={styles.pagerLink} href={`/track-record?page=${page - 1}`}>
          ‹ 上一页
        </Link>
      ) : (
        <span className={styles.pagerDisabled}>‹ 上一页</span>
      )}
      <span className={styles.pagerInfo}>
        第 <span className="num">{page}</span> / <span className="num">{totalPages}</span> 页
      </span>
      {page < totalPages ? (
        <Link className={styles.pagerLink} href={`/track-record?page=${page + 1}`}>
          下一页 ›
        </Link>
      ) : (
        <span className={styles.pagerDisabled}>下一页 ›</span>
      )}
    </nav>
  );
}

/* ── 数据区块(Suspense 内)────────────────────────────── */

async function TrackRecordContent({ page }: { page: number }) {
  let data: TrackRecordResponse;
  try {
    data = await serverGet<TrackRecordResponse>(
      `/api/v1/track-record?limit=${PAGE_SIZE}&offset=${(page - 1) * PAGE_SIZE}`,
      { revalidate: 120 },
    );
  } catch (err) {
    return (
      <div className={styles.errorBox}>
        <div className={styles.errorTitle}>数据暂时无法加载</div>
        <p>
          数据暂时取不到，请稍后再看。
          <br />
          <span className={styles.errorDetail}>
            {err instanceof Error ? err.message : String(err)}
          </span>
        </p>
      </div>
    );
  }

  if (data.total === 0) {
    return (
      <>
        <CriteriaCard />
        <div className={styles.emptyCard}>
          <div className={styles.emptyTitle}>暂时没有满足条件的正式样本</div>
          <p className={styles.emptyText}>
            {data.empty_reason ?? "正式样本 = 开球前生成、发布并锁定的预测。"}
          </p>
          <p className={styles.emptyText}>
            正式预测还没开始跑。开跑之后，每场的预测会在开球前锁死，赛后按时间往这儿累加，看错的也照样留着。在那之前这里就是空的，不拿演示数据凑。
          </p>
        </div>
      </>
    );
  }

  if (data.samples.length === 0) {
    return (
      <>
        <CriteriaCard />
        <div className={styles.emptyCard}>
          <div className={styles.emptyTitle}>页码超出范围</div>
          <p className={styles.emptyText}>
            共 <span className="num">{data.total}</span> 条记录。
            <Link href="/track-record" className={styles.backLink}>
              返回第 1 页
            </Link>
          </p>
        </div>
      </>
    );
  }

  const versionIds = Array.from(new Set(data.samples.map((s) => s.model_version_id)));

  return (
    <>
      <MetricsRow data={data} />
      <p className={styles.metaLine}>
        模型版本：<span className="num">{versionIds.join(" / ")}</span> · 覆盖开球区间{" "}
        <LocalTime utc={data.samples[data.samples.length - 1].kickoff_at_utc} mode="date" /> –{" "}
        <LocalTime utc={data.samples[0].kickoff_at_utc} mode="date" />
      </p>
      <CriteriaCard />
      <div className={styles.tableCard}>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={styles.thLeft}>开球时间</th>
                <th className={styles.thLeft}>对阵</th>
                <th>主胜</th>
                <th>平局</th>
                <th>客胜</th>
                <th>预测</th>
                <th>实际</th>
                <th>命中</th>
                <th className={styles.statusCell}>状态</th>
              </tr>
            </thead>
            <tbody>
              {data.samples.map((s) => (
                <SampleRow key={`${s.match_id}-${s.prediction_hash}`} s={s} />
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <Pager page={page} total={data.total} />
    </>
  );
}

function Skeleton() {
  return (
    <div aria-hidden="true">
      <div className={styles.metricsGrid}>
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className={`${styles.metricCard} ${styles.skeletonCard}`} />
        ))}
      </div>
      <div className={styles.skeletonBlock} />
      <div className={styles.skeletonTable} />
    </div>
  );
}

/* ── 页面入口 ───────────────────────────────────────────── */

export default async function TrackRecordPage({
  searchParams,
}: {
  searchParams: Promise<{ page?: string }>;
}) {
  const sp = await searchParams;
  const parsed = parseInt(sp.page ?? "1", 10);
  const page = Number.isFinite(parsed) && parsed > 0 ? parsed : 1;

  return (
    <main className={styles.page}>
      <div className={styles.header}>
        <h1 className={styles.title}>公开预测战绩</h1>
      </div>
      <p className={styles.subtitle}>每场预测都在开球前锁死，赛后照实结算。看对的看错的都在这张表里。</p>
      <Suspense key={page} fallback={<Skeleton />}>
        <TrackRecordContent page={page} />
      </Suspense>
    </main>
  );
}
