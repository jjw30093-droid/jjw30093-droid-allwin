import type { Metadata } from "next";
import { notFound } from "next/navigation";
import {
  serverGetOptional,
  type MatchDetailResponse,
  type PredictionResponse,
} from "@/lib/api-v1";
import { PredictionCard } from "@/components/matches/PredictionCard";
import { OddsTimeline } from "@/components/matches/OddsTimeline";
import { CooccurrenceSection } from "@/components/matches/CooccurrenceSection";
import { LocalTime } from "@/components/matches/LocalTime";
import type { ChartSpec } from "@/components/charts/SpecCharts";
import { ChartWithSummary } from "@/components/matches/ChartWithSummary";
import { LEAGUE_ZH, STATUS_ZH, formatDateZh } from "@/components/matches/zh";
import styles from "./match-detail.module.css";

/**
 * 比赛详情页(宪法 §11.1 固定顺序):
 * 1 头部 → 2 概率卡 → 3 证据/反向证据 → 4 可视化 → 5 赔率时间轴 → 6 同期事件 → 7 模型与登记信息。
 *
 * 免费/付费边界:本 server component 只请求匿名投影(受限字段物理不存在);
 * 会员增强(完整概率/完整赔率历史)全部由客户端组件带会话 cookie 重拉。
 */

// /analysis 无 response_model,按 backend/api/routes_public.py + backend/studio/bundle.py 真实结构手写
interface EvidenceItem {
  side?: string;
  kind: string;
  text: string;
}
interface SourceNote {
  kind: string;
  text: string;
}
interface AnalysisBundle {
  bundle_version: string | number;
  data_cutoff_at: string | null;
  model_version: string | null;
  evidence: EvidenceItem[];
  counter_evidence: EvidenceItem[];
  uncertainty: EvidenceItem[];
  chart_specs: ChartSpec[];
  source_notes: SourceNote[];
  cooccurrence_count: number;
}

type FormEntry = MatchDetailResponse["home_form"][number];

export async function generateMetadata({
  params,
}: {
  params: Promise<{ matchId: string }>;
}): Promise<Metadata> {
  const { matchId } = await params;
  const detail = await serverGetOptional<MatchDetailResponse>(
    `/api/v1/matches/${matchId}`,
    { revalidate: 300 },
  ).catch(() => null);
  if (!detail) return { title: "比赛详情 — 欧赢 allwin" };
  const m = detail.match;
  return {
    title: `${m.home.name} vs ${m.away.name} — 欧赢 allwin`,
    description: `${m.season} ${LEAGUE_ZH[m.league_id] ?? ""} 赛前分析:数据、模型概率与不确定性`,
  };
}

function FormList({ title, entries }: { title: string; entries: FormEntry[] }) {
  return (
    <div className={styles.formCol}>
      <h3 className={styles.formTitle}>{title}</h3>
      {entries.length === 0 ? (
        <p className={styles.emptyText}>暂无近期完赛记录</p>
      ) : (
        <ul className={styles.formList}>
          {entries.map((f) => (
            <li key={f.match_id} className={styles.formItem}>
              <span
                className={`${styles.formBadge} ${
                  f.result === "W"
                    ? styles.badgeW
                    : f.result === "D"
                      ? styles.badgeD
                      : styles.badgeL
                }`}
              >
                {f.result === "W" ? "胜" : f.result === "D" ? "平" : "负"}
              </span>
              <span className={styles.formScore}>
                <b className="num">
                  {f.goals_for}–{f.goals_against}
                </b>
              </span>
              <span className={styles.formOpp}>
                {f.venue === "home" ? "主" : "客"} vs {f.opponent.name}
              </span>
              <span className={styles.formDate}>{formatDateZh(f.date_utc)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function EvidenceList({
  title,
  items,
  tone,
  emptyText,
}: {
  title: string;
  items: EvidenceItem[];
  tone: "pro" | "con" | "warn";
  emptyText: string;
}) {
  return (
    <div className={styles.evidenceCol}>
      <h3
        className={`${styles.evidenceTitle} ${
          tone === "pro" ? styles.tonePro : tone === "con" ? styles.toneCon : styles.toneWarn
        }`}
      >
        {title}
      </h3>
      {items.length === 0 ? (
        <p className={styles.emptyText}>{emptyText}</p>
      ) : (
        <ul className={styles.evidenceList}>
          {items.map((e, i) => (
            <li key={`${e.kind}-${i}`}>{e.text}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default async function MatchDetailPage({
  params,
}: {
  params: Promise<{ matchId: string }>;
}) {
  const { matchId } = await params;
  const idNum = Number(matchId);
  if (!Number.isInteger(idNum) || idNum <= 0) notFound();

  let detail: MatchDetailResponse | null = null;
  let loadError = false;
  try {
    detail = await serverGetOptional<MatchDetailResponse>(`/api/v1/matches/${idNum}`, {
      revalidate: 120,
    });
  } catch {
    loadError = true;
  }

  if (loadError) {
    return (
      <main className={styles.page}>
        <div className={styles.errorBox}>
          数据暂时无法加载,请稍后重试(serving API 未响应)。
        </div>
      </main>
    );
  }
  if (!detail) notFound();
  const m = detail.match;

  const [prediction, analysis] = await Promise.all([
    serverGetOptional<PredictionResponse>(`/api/v1/matches/${idNum}/prediction`).catch(
      () => null,
    ),
    serverGetOptional<AnalysisBundle>(`/api/v1/matches/${idNum}/analysis`).catch(() => null),
  ]);

  const finished = m.status === "Finish" && m.home_score != null && m.away_score != null;

  return (
    <main className={styles.page}>
      {/* 1. 比赛头部 */}
      <header className={styles.header}>
        <div className={styles.metaRow}>
          <span className={styles.chip}>{LEAGUE_ZH[m.league_id] ?? `联赛 ${m.league_id}`}</span>
          <span className={styles.chip}>{m.season}</span>
          {m.round && <span className={styles.chip}>第 {m.round} 轮</span>}
          <span className={styles.chipStatus}>{STATUS_ZH[m.status] ?? m.status}</span>
        </div>
        <div className={styles.teamsRow}>
          <div className={styles.teamName}>{m.home.name}</div>
          <div className={styles.scoreBox}>
            {finished ? (
              <span className={`${styles.score} num`}>
                {m.home_score} – {m.away_score}
              </span>
            ) : (
              <span className={styles.vs}>VS</span>
            )}
          </div>
          <div className={styles.teamName}>{m.away.name}</div>
        </div>
        <p className={styles.dateLine}>
          比赛日:{formatDateZh(m.date_utc)}(UTC 口径,开球时刻精确到比赛日)
          {detail.data_updated_at && (
            <>
              {" · 数据更新:"}
              <LocalTime iso={detail.data_updated_at} />
            </>
          )}
        </p>
      </header>

      {/* 2. 概率卡(免费最高一项 / 会员完整) */}
      <section className={styles.section} aria-label="模型概率">
        <PredictionCard matchId={idNum} initial={prediction} />
      </section>

      {/* 3. 支持证据与反向证据 */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>证据与不确定性</h2>
        {analysis ? (
          <>
            <div className={styles.evidenceGrid}>
              <EvidenceList
                title="支持证据"
                tone="pro"
                items={analysis.evidence}
                emptyText="暂无足够数据形成支持证据"
              />
              <EvidenceList
                title="反向证据"
                tone="con"
                items={analysis.counter_evidence}
                emptyText="暂无明显反向证据"
              />
            </div>
            <EvidenceList
              title="不确定性提示"
              tone="warn"
              items={analysis.uncertainty}
              emptyText="—"
            />
          </>
        ) : (
          <p className={styles.emptyText}>分析内容暂时无法加载。</p>
        )}
      </section>

      {/* 4. 可视化:近期表现 + chart_specs */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>数据可视化</h2>
        <div className={styles.formGrid}>
          <FormList title={`${m.home.name} 近期表现`} entries={detail.home_form} />
          <FormList title={`${m.away.name} 近期表现`} entries={detail.away_form} />
        </div>
        {analysis && analysis.chart_specs.length > 0 ? (
          <div className={styles.chartGrid}>
            {analysis.chart_specs.map((spec) => (
              <figure key={spec.id} className={styles.chartCard}>
                <ChartWithSummary
                  spec={spec}
                  titleClassName={styles.chartTitle}
                  summaryClassName={styles.chartSummary}
                />
              </figure>
            ))}
          </div>
        ) : (
          <p className={styles.emptyText}>该场比赛暂无可视化图表数据。</p>
        )}
      </section>

      {/* 5. 赔率时间轴(免费延迟摘要 / Premium 完整历史,客户端拉取) */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>赔率时间轴</h2>
        <OddsTimeline matchId={idNum} />
      </section>

      {/* 6. 同期事件(时间共现,不声称因果) */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>同期事件</h2>
        <CooccurrenceSection matchId={idNum} />
      </section>

      {/* 7. 模型版本 / cutoff / 局限 / 登记信息 */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>模型与登记信息</h2>
        <dl className={styles.metaList}>
          <div>
            <dt>模型版本</dt>
            <dd>{analysis?.model_version ?? "暂无已发布预测"}</dd>
          </div>
          <div>
            <dt>数据截止</dt>
            <dd>
              {analysis?.data_cutoff_at ? <LocalTime iso={analysis.data_cutoff_at} /> : "—"}
            </dd>
          </div>
          {finished && (
            <div>
              <dt>赛后记录</dt>
              <dd>
                已完赛,赛果{" "}
                <b className="num">
                  {m.home_score}–{m.away_score}
                </b>
                ;正式预测的赛后评估见「公开战绩」页
              </dd>
            </div>
          )}
        </dl>
        {analysis && analysis.source_notes.length > 0 && (
          <ul className={styles.noteList}>
            {analysis.source_notes.map((n, i) => (
              <li key={`${n.kind}-${i}`}>{n.text}</li>
            ))}
          </ul>
        )}
        <p className={styles.disclaimer}>
          本页为数据研究内容:模型概率存在不确定性,历史表现不代表未来;不构成任何投注建议。
        </p>
      </section>
    </main>
  );
}
