/**
 * 比赛详情页主体——纯展示,无取数。
 *
 * 被两个环境共用(刻意不写 "use client",随导入方环境走):
 * - app/matches/[matchId]/page.tsx(RSC,匿名投影,免费联赛路径);
 * - components/matches/MemberMatchDetail.tsx(client,带会话 cookie 重取,
 *   付费联赛路径——修复审计 B1:此前详情页对 Pro/Premium 一律 404)。
 * 两条路径渲染完全相同的 JSX,免费/付费差异只体现在传入的数据投影上
 * (受限字段物理不存在,不是 CSS 遮挡)。
 *
 * 2026-08-14 重设计(Claude Design 定稿,design_handoff_match_detail):
 * 两种形态按 GET /matches/{id}/report 的 available 判定,不按 status 硬编码:
 * - available===false(含 InPlay,事实表通常还没写入):头部版式 A
 *   (MatchHeaderPre)+ 赛前三 tab(看点/数据/赔率),内容分组见 OverviewGroups;
 * - available===true:头部版式 B(MatchHeaderFinished)+ 五 tab(总览/射门/
 *   统计/阵容/事件),总览 = 三组内容依次平铺(不再分 tab)。
 * 内容一项不减,只是从"6 段竖着铺"重组为"按用途分组"。
 */

import Link from "next/link";
import type {
  GetJson,
  MatchDetailResponse,
  MatchReportResponse,
  MatchSummary,
} from "@/lib/api-v1";
import { RecordVisit } from "@/components/matches/RecordVisit";
import { OddsTimeline } from "@/components/matches/OddsTimeline";
import { CooccurrenceSection } from "@/components/matches/CooccurrenceSection";
import { MarketCardsSection } from "@/components/matches/MarketCardsSection";
import { LocalTime } from "@/components/matches/LocalTime";
import { ChartWithSummary } from "@/components/matches/ChartWithSummary";
import { MatchHeaderPre } from "@/components/matches/MatchHeaderPre";
import { MatchHeaderFinished } from "@/components/matches/MatchHeaderFinished";
import { MatchTabs } from "@/components/matches/MatchTabs";
import { MatchPreTabs } from "@/components/matches/MatchPreTabs";
import { MatchLineupSection } from "@/components/matches/MatchLineupSection";
import { MatchStatsSection } from "@/components/matches/MatchStatsSection";
import { MatchShotsSection } from "@/components/matches/MatchShotsSection";
import { MatchEventsSection } from "@/components/matches/MatchEventsSection";
import { formatDateZh } from "@/components/matches/zh";
import styles from "@/app/matches/[matchId]/match-detail.module.css";

export type AnalysisBundle = GetJson<"/api/v1/matches/{match_id}/analysis">;
type FormEntry = MatchDetailResponse["home_form"][number];

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className={styles.sectionTitle}>
      <span className={styles.sectionBar} aria-hidden />
      {children}
    </h2>
  );
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

/** 一方近 5 场的战绩汇总:胜平负数量 + 场均入球。 */
function formSummary(entries: FormEntry[]): { w: number; d: number; l: number; avgGoals: number } | null {
  if (entries.length === 0) return null;
  let w = 0, d = 0, l = 0, goals = 0;
  for (const f of entries) {
    if (f.result === "W") w += 1;
    else if (f.result === "D") d += 1;
    else l += 1;
    goals += f.goals_for;
  }
  return { w, d, l, avgGoals: goals / entries.length };
}

/**
 * 本场看点(赛前第一屏,替代旧"30秒速览"的概率结论)。模型未经真实数据
 * 训练前不可用,详情页不渲染任何概率——结论改为基于近期战绩现算的一句
 * 真实数据对比,双方均无数据时如实收窄,不铺"暂无正式预测"这类模型语言。
 * 2026-08-14 起分 tab 展示,原footer的"查看详细数据↓"锚点已删除
 * (分 tab 后没有"往下滚"这件事了)。
 */
function QuickView({ detail }: { detail: MatchDetailResponse }) {
  const m = detail.match;
  const home = formSummary(detail.home_form);
  const away = formSummary(detail.away_form);

  return (
    <section className={styles.quickView} aria-label="本场看点" data-testid="quick-view">
      <div className={styles.quickRecoRow}>
        {detail.reco_published ? (
          <Link href="/reco?tab=daily" className={styles.quickRecoOn}>
            本场有已发布的每日精选 →
          </Link>
        ) : (
          <span className={styles.quickRecoOff}>推荐待发布</span>
        )}
      </div>

      {home && away ? (
        <p className={styles.quickFormLine}>
          {m.home.name} 近{detail.home_form.length}场{" "}
          <b className="num">{home.w}胜{home.d}平{home.l}负</b>,场均入球{" "}
          <b className="num">{home.avgGoals.toFixed(1)}</b>;{m.away.name} 近
          {detail.away_form.length}场{" "}
          <b className="num">{away.w}胜{away.d}平{away.l}负</b>,场均入球{" "}
          <b className="num">{away.avgGoals.toFixed(1)}</b>
        </p>
      ) : home ? (
        <p className={styles.quickFormLine}>
          {m.home.name} 近{detail.home_form.length}场{" "}
          <b className="num">{home.w}胜{home.d}平{home.l}负</b>,场均入球{" "}
          <b className="num">{home.avgGoals.toFixed(1)}</b>;{m.away.name} 暂无近期完赛记录
        </p>
      ) : away ? (
        <p className={styles.quickFormLine}>
          {m.away.name} 近{detail.away_form.length}场{" "}
          <b className="num">{away.w}胜{away.d}平{away.l}负</b>,场均入球{" "}
          <b className="num">{away.avgGoals.toFixed(1)}</b>;{m.home.name} 暂无近期完赛记录
        </p>
      ) : (
        <p className={styles.quickNoTip}>本场暂无历史交锋与近期数据,仅有赛程信息。</p>
      )}
    </section>
  );
}

/** 看点 tab:本场看点(仅未完赛)→ 数据倾向(市场卡)。 */
function HighlightsGroup({
  idNum,
  detail,
  finished,
}: {
  idNum: number;
  detail: MatchDetailResponse;
  finished: boolean;
}) {
  return (
    <>
      {!finished && <QuickView detail={detail} />}
      <section className={styles.section}>
        <SectionTitle>数据倾向</SectionTitle>
        <MarketCardsSection matchId={idNum} />
      </section>
    </>
  );
}

/** 数据 tab:近期表现(两队各一张卡)→ chart_specs 图表。 */
function DataGroup({
  detail,
  analysis,
}: {
  detail: MatchDetailResponse;
  analysis: AnalysisBundle | null;
}) {
  const m = detail.match;
  return (
    <section className={styles.section}>
      <SectionTitle>数据可视化</SectionTitle>
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
  );
}

/** 赔率 tab:赔率快照 → 关键变化 → 数据来源与说明(折叠)。 */
function OddsGroup({
  idNum,
  detail,
  analysis,
  finished,
}: {
  idNum: number;
  detail: MatchDetailResponse;
  analysis: AnalysisBundle | null;
  finished: boolean;
}) {
  const m = detail.match;
  return (
    <>
      <section className={styles.section}>
        <SectionTitle>赔率快照</SectionTitle>
        <OddsTimeline matchId={idNum} />
      </section>

      {/* 关键变化(时间共现,不声称因果)标题由组件自带,无内容时整体不渲染。 */}
      <CooccurrenceSection matchId={idNum} />

      <section className={styles.section}>
        <details className={styles.metaDetails}>
          <summary className={styles.metaSummary}>数据来源与说明</summary>
          <dl className={styles.metaList}>
            <div>
              <dt>模型版本</dt>
              <dd>{analysis?.model_version ?? "暂无已发布预测"}</dd>
            </div>
            <div>
              <dt>数据更新于</dt>
              <dd>
                {analysis?.data_cutoff_at ? <LocalTime iso={analysis.data_cutoff_at} /> : "—"}
              </dd>
            </div>
            <div>
              <dt>赛季</dt>
              <dd>
                {m.season} 赛季 · 比赛日 {formatDateZh(m.date_utc)}
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
                  ;正式预测的赛后评估见「模型公开记录」页
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
        </details>
        <p className={styles.disclaimer}>
          本页为数据研究内容:历史表现不代表未来;不构成任何投注建议。
        </p>
      </section>
    </>
  );
}

/** 已完赛「总览」tab:三组内容依次平铺,不再分 tab(不渲染本场看点)。 */
function OverviewPanel({
  idNum,
  detail,
  analysis,
  finished,
}: {
  idNum: number;
  detail: MatchDetailResponse;
  analysis: AnalysisBundle | null;
  finished: boolean;
}) {
  return (
    <>
      <HighlightsGroup idNum={idNum} detail={detail} finished={finished} />
      <DataGroup detail={detail} analysis={analysis} />
      <OddsGroup idNum={idNum} detail={detail} analysis={analysis} finished={finished} />
    </>
  );
}

export function MatchDetailBody({
  idNum,
  detail,
  analysis,
  report = null,
  returnTo,
  returnLabel,
  previousMatch,
  nextMatch,
}: {
  idNum: number;
  detail: MatchDetailResponse;
  analysis: AnalysisBundle | null;
  /** /matches/{id}/report(完赛事实):available 时渲染 总览/射门/统计/阵容/事件 五 tab */
  report?: MatchReportResponse | null;
  returnTo: string;
  returnLabel: string;
  previousMatch: MatchSummary | null;
  nextMatch: MatchSummary | null;
}) {
  const m = detail.match;
  const finished = m.status === "Finish" && m.home_score != null && m.away_score != null;
  const factReport = report?.available ? report : null;

  return (
    <main className={styles.page}>
      <RecordVisit matchId={idNum} />
      <nav className={styles.contextNav} aria-label="比赛上下文导航">
        <Link href={returnTo}>← {returnLabel}</Link>
        <span>
          {previousMatch && (
            <Link href={`/matches/${previousMatch.match_id}`}>上一场</Link>
          )}
          {nextMatch && <Link href={`/matches/${nextMatch.match_id}`}>下一场</Link>}
          <Link href="/matches?status=upcoming&window=7d" className={styles.contextNavWide}>
            查看本周其他比赛
          </Link>
        </span>
      </nav>

      {factReport ? (
        <MatchHeaderFinished detail={detail} />
      ) : (
        <MatchHeaderPre match={m} />
      )}
      {/* 采集轮询计划、观测点数等内部审计信息不进首屏(下方「数据来源与
          说明」与后台仍可溯源)。模型概率未经真实训练前不可用,详情页不再
          渲染概率或其来源说明。 */}
      {m.sync_state === "STALE" && (
        <p className={styles.staleNotice}>
          数据已过期;
          {m.next_planned_sync_at ? "正在等待计划采集。" : "正在等待采集恢复。"}
        </p>
      )}

      {factReport ? (
        <MatchTabs
          overview={
            <OverviewPanel idNum={idNum} detail={detail} analysis={analysis} finished={finished} />
          }
          shots={
            <MatchShotsSection
              shots={factReport.shots}
              homeName={m.home.name}
              awayName={m.away.name}
              homeScore={m.home_score}
              awayScore={m.away_score}
            />
          }
          lineup={
            <MatchLineupSection
              lineups={factReport.lineups}
              homeName={m.home.name}
              awayName={m.away.name}
            />
          }
          stats={
            <MatchStatsSection
              teamStats={factReport.team_stats}
              shots={factReport.shots}
              playerStats={factReport.player_stats}
              homeName={m.home.name}
              awayName={m.away.name}
            />
          }
          events={
            <MatchEventsSection
              events={factReport.events}
              homeName={m.home.name}
              awayName={m.away.name}
            />
          }
        />
      ) : (
        <MatchPreTabs
          highlights={<HighlightsGroup idNum={idNum} detail={detail} finished={finished} />}
          data={<DataGroup detail={detail} analysis={analysis} />}
          odds={<OddsGroup idNum={idNum} detail={detail} analysis={analysis} finished={finished} />}
        />
      )}
    </main>
  );
}
