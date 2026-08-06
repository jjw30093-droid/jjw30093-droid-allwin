/**
 * 比赛详情页主体(宪法 §11.1 固定顺序)——纯展示,无取数。
 *
 * 被两个环境共用(刻意不写 "use client",随导入方环境走):
 * - app/matches/[matchId]/page.tsx(RSC,匿名投影,免费联赛路径);
 * - components/matches/MemberMatchDetail.tsx(client,带会话 cookie 重取,
 *   付费联赛路径——修复审计 B1:此前详情页对 Pro/Premium 一律 404)。
 * 两条路径渲染完全相同的 JSX,免费/付费差异只体现在传入的数据投影上
 * (受限字段物理不存在,不是 CSS 遮挡)。
 */

import Link from "next/link";
import type { GetJson, MatchDetailResponse, MatchSummary, PredictionResponse } from "@/lib/api-v1";
import { PredictionCard } from "@/components/matches/PredictionCard";
import { OddsTimeline } from "@/components/matches/OddsTimeline";
import { CooccurrenceSection } from "@/components/matches/CooccurrenceSection";
import { LocalTime } from "@/components/matches/LocalTime";
import { ChartWithSummary } from "@/components/matches/ChartWithSummary";
import { TeamBadge } from "@/components/teams/TeamBadge";
import { LEAGUE_ZH, STATUS_ZH, formatDateZh } from "@/components/matches/zh";
import { syncStateLabel } from "@/lib/product-status";
import styles from "@/app/matches/[matchId]/match-detail.module.css";

export type AnalysisBundle = GetJson<"/api/v1/matches/{match_id}/analysis">;
type EvidenceLike = Pick<AnalysisBundle["evidence"][number], "kind" | "text">;
type FormEntry = MatchDetailResponse["home_form"][number];

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
  items: EvidenceLike[];
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

export function MatchDetailBody({
  idNum,
  detail,
  prediction,
  analysis,
  returnTo,
  returnLabel,
  previousMatch,
  nextMatch,
}: {
  idNum: number;
  detail: MatchDetailResponse;
  prediction: PredictionResponse | null;
  analysis: AnalysisBundle | null;
  returnTo: string;
  returnLabel: string;
  previousMatch: MatchSummary | null;
  nextMatch: MatchSummary | null;
}) {
  const m = detail.match;
  const finished = m.status === "Finish" && m.home_score != null && m.away_score != null;
  return (
    <main className={styles.page}>
      <nav className={styles.contextNav} aria-label="比赛上下文导航">
        <Link href={returnTo}>← {returnLabel}</Link>
        <span>
          {previousMatch && (
            <Link href={`/matches/${previousMatch.match_id}`}>同联赛上一场</Link>
          )}
          {nextMatch && <Link href={`/matches/${nextMatch.match_id}`}>同联赛下一场</Link>}
          <Link href="/matches?status=upcoming&window=7d">未来七天更多比赛</Link>
        </span>
      </nav>
      {/* 1. 比赛头部 */}
      <header className={styles.header}>
        <div className={styles.metaRow}>
          <span className={styles.chip}>{LEAGUE_ZH[m.league_id] ?? `联赛 ${m.league_id}`}</span>
          <span className={styles.chip}>{m.season}</span>
          {m.round && <span className={styles.chip}>第 {m.round} 轮</span>}
          <span className={styles.chipStatus}>{STATUS_ZH[m.status] ?? m.status}</span>
          {m.sync_state && (
            <span
              className={`${styles.chipStatus} ${
                m.sync_state === "STALE" ? styles.chipStale : ""
              }`}
            >
              {syncStateLabel(m.sync_state)}
            </span>
          )}
        </div>
        <div className={styles.teamsRow}>
          <div className={styles.teamBlock}>
            <TeamBadge
              teamName={m.home.name}
              crestUrl={m.home.crest_url}
              size={56}
              eager
            />
            <div className={styles.teamName}>{m.home.name}</div>
          </div>
          <div className={styles.scoreBox}>
            {finished ? (
              <span className={`${styles.score} num`}>
                {m.home_score} – {m.away_score}
              </span>
            ) : (
              <span className={styles.vs}>VS</span>
            )}
          </div>
          <div className={styles.teamBlock}>
            <TeamBadge
              teamName={m.away.name}
              crestUrl={m.away.crest_url}
              size={56}
              eager
            />
            <div className={styles.teamName}>{m.away.name}</div>
          </div>
        </div>
        <p className={styles.dateLine}>
          比赛日:{formatDateZh(m.date_utc)}
          {m.kickoff_at_utc && (
            <>
              {" · 开球:"}
              <LocalTime iso={m.kickoff_at_utc} />
            </>
          )}
          {detail.data_updated_at && (
            <>
              {" · 数据更新:"}
              <LocalTime iso={detail.data_updated_at} />
            </>
          )}
          {m.last_success_sync_at && (
            <>
              {" · 最近成功同步:"}
              <LocalTime iso={m.last_success_sync_at} />
            </>
          )}
          {m.next_planned_sync_at && (
            <>
              {" · 下次计划:"}
              <LocalTime iso={m.next_planned_sync_at} />
            </>
          )}
        </p>
        {(m.probability_source || m.odds_observation_count != null) && (
          <p className={styles.dataStateLine}>
            概率来源:{m.probability_source ?? "UNAVAILABLE"} · 赛前赔率观测:
            {m.odds_observation_count ?? 0} 个点
          </p>
        )}
        {m.sync_state === "STALE" && (
          <p className={styles.staleNotice}>
            数据已过期；
            {m.next_planned_sync_at ? "正在等待计划采集。" : "正在等待采集恢复。"}
          </p>
        )}
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
