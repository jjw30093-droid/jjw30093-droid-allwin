/**
 * 比赛详情页主体——纯展示,无取数。
 *
 * 被两个环境共用(刻意不写 "use client",随导入方环境走):
 * - app/matches/[matchId]/page.tsx(RSC,服务端匿名取数成功时走这条路径);
 * - components/matches/MemberMatchDetail.tsx(client,服务端取数失败/比赛
 *   不存在时接手,浏览器重新请求)。
 * 两条路径渲染完全相同的 JSX、消费完全相同的数据形状——2026-08-16 权限
 * 口径修正后,比赛内容对任何人(含匿名)恒完整,不再有身份分层投影。
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
  MatchPreviewResponse,
  MatchReportResponse,
  MatchSummary,
} from "@/lib/api-v1";
import { RecordVisit } from "@/components/matches/RecordVisit";
import { OddsTimeline } from "@/components/matches/OddsTimeline";
import { CooccurrenceSection } from "@/components/matches/CooccurrenceSection";
import { MarketCardsSection } from "@/components/matches/MarketCardsSection";
import { LocalTime } from "@/components/matches/LocalTime";
import { MatchHeaderPre } from "@/components/matches/MatchHeaderPre";
import { MatchInfoCard } from "@/components/matches/MatchInfoCard";
import { MatchHeaderFinished } from "@/components/matches/MatchHeaderFinished";
import { MatchTabs } from "@/components/matches/MatchTabs";
import { MatchPreTabs } from "@/components/matches/MatchPreTabs";
import { MatchLineupSection } from "@/components/matches/MatchLineupSection";
import { MatchStatsSection } from "@/components/matches/MatchStatsSection";
import { MatchShotsSection } from "@/components/matches/MatchShotsSection";
import { MatchEventsSection } from "@/components/matches/MatchEventsSection";
import { MatchDataTabs } from "@/components/matches/MatchDataTabs";
import { ChartWithSummary } from "@/components/matches/ChartWithSummary";
import { ProjectedLineupSection } from "@/components/matches/ProjectedLineupSection";
import { TeamStyleQuadrant } from "@/components/matches/TeamStyleQuadrant";
import { AttackChainSection } from "@/components/matches/AttackChainSection";
import { PossessionControlSection } from "@/components/matches/PossessionControlSection";
import { DefensivePressureSection } from "@/components/matches/DefensivePressureSection";
import { MatchupSection } from "@/components/matches/MatchupSection";
import {
  AttackSourceCard,
  AttackSourceSection,
  GoalkeeperSection,
  KeyPlayersSection,
} from "@/components/matches/MatchDataModules";
import { matchDayZh } from "@/components/matches/zh";
import styles from "@/app/matches/[matchId]/match-detail.module.css";

export type AnalysisBundle = GetJson<"/api/v1/matches/{match_id}/analysis">;
type PreviewWindow = NonNullable<MatchPreviewResponse["home_window"]>;
type AttackSourceRow = MatchPreviewResponse["attack_sources"]["home"][number];

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className={styles.sectionTitle}>
      <span className={styles.sectionBar} aria-hidden />
      {children}
    </h2>
  );
}

/**
 * 本场看点(赛前第一屏)。验收返工五:不再展示"近 N 场胜平负/场均入球"
 * ——用户已明确表示不要这个模块(同类网站已大量提供,不是本站差异化),
 * 不用另一套近期战绩换皮替代。只保留推荐发布状态提示;模型未经真实数据
 * 训练前不渲染任何概率。
 */
function QuickView({ detail }: { detail: MatchDetailResponse }) {
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
      <MatchInfoCard match={detail.match} />
      {!finished && <QuickView detail={detail} />}
      <section className={styles.section}>
        <SectionTitle>数据倾向</SectionTitle>
        <MarketCardsSection matchId={idNum} />
      </section>
    </>
  );
}

/** 「近 N 场」真实日期区间文案——两队各自独立的窗口,不合并成一句话
 * (历史场次不同时合并会掩盖样本更少的那一队,见 MatchPreviewWindowDTO 注释)。 */
function windowNote(
  homeName: string,
  awayName: string,
  homeWindow: PreviewWindow | null | undefined,
  awayWindow: PreviewWindow | null | undefined,
): string {
  const part = (name: string, w: PreviewWindow | null | undefined) =>
    w ? `${name}近${w.matches}场(${w.from} 至 ${w.to})` : `${name}暂无可比较的历史比赛`;
  return `${part(homeName, homeWindow)};${part(awayName, awayWindow)}。`;
}

/** 进攻来源卡片的摘要句——机械地从真实数据里挑"每脚效率最高的来源",
 * 不编造解读;没有任何来源同时有射门与 xG 时给通用提示。 */
function attackSourceNote(rows: AttackSourceRow[]): string {
  if (rows.length === 0) return "该队近 5 场没有射门数据。";
  const withXg = rows.filter((r) => r.xg != null && r.shots > 0);
  if (withXg.length === 0) return "暂无各来源的 xG 拆分。";
  const top = [...withXg].sort(
    (a, b) => (b.xg as number) / b.shots - (a.xg as number) / a.shots,
  )[0];
  return `${top.label}每脚效率最高,平均 xG ${((top.xg as number) / top.shots).toFixed(3)}/脚。`;
}

/** 数据 tab:近期表现(两队各一张卡)→ 阵容/风格/球员/射门四个子 tab。 */
function DataGroup({
  detail,
  preview,
  analysis,
}: {
  detail: MatchDetailResponse;
  preview: MatchPreviewResponse | null;
  analysis: AnalysisBundle | null;
}) {
  const m = detail.match;
  const homeName = m.home.name;
  const awayName = m.away.name;
  const homeTeamId = m.home.team_id;
  const awayTeamId = m.away.team_id;
  // 两队各自最近 5 场(同联赛口径优先)射门分布,后端已算好、拼进
  // /analysis 的 chart_specs(backend/studio/bundle.py)——组件本身早就写好
  // (ChartWithSummary + SpecChart 的 shot_map_explorer 分支),此前只是没有
  // 任何页面把这个 tab 接上,不是数据/组件缺失。两队都没有覆盖时诚实展示
  // 空态,不假装有数据。
  const shotMapSpec = analysis?.chart_specs.find((c) => c.type === "shot_map_explorer") ?? null;

  return (
    <section className={styles.section}>
      <SectionTitle>数据可视化</SectionTitle>
      {!preview ? (
        <p className={styles.emptyText}>数据暂时无法加载。</p>
      ) : (
        <MatchDataTabs
          lineup={
            <ProjectedLineupSection
              homeName={homeName}
              awayName={awayName}
              lineupType={preview.lineups.lineup_type ?? null}
              source={preview.lineups.source ?? null}
              observedAt={preview.lineups.observed_at ?? null}
              home={preview.lineups.home ?? null}
              away={preview.lineups.away ?? null}
              homeSidelined={preview.sidelined.home}
              awaySidelined={preview.sidelined.away}
            />
          }
          style={
            <>
              <MatchupSection
                homeName={homeName}
                awayName={awayName}
                home={preview.matchup_profiles.home}
                away={preview.matchup_profiles.away}
              />
              <AttackChainSection
                homeName={homeName}
                awayName={awayName}
                home={preview.attack_chains.home}
                away={preview.attack_chains.away}
              />
              <PossessionControlSection
                homeName={homeName}
                awayName={awayName}
                home={preview.possession_controls.home}
                away={preview.possession_controls.away}
              />
              <DefensivePressureSection
                homeName={homeName}
                awayName={awayName}
                home={preview.defensive_pressures.home}
                away={preview.defensive_pressures.away}
              />
              {homeTeamId != null && awayTeamId != null ? (
                <TeamStyleQuadrant
                  views={preview.style_views}
                  homeTeamId={homeTeamId}
                  awayTeamId={awayTeamId}
                  homeName={homeName}
                  awayName={awayName}
                  windowNote={windowNote(homeName, awayName, preview.home_window, preview.away_window)}
                />
              ) : null}
              <AttackSourceSection>
                <AttackSourceCard
                  teamName={homeName}
                  rows={preview.attack_sources.home}
                  note={attackSourceNote(preview.attack_sources.home)}
                />
                <AttackSourceCard
                  teamName={awayName}
                  rows={preview.attack_sources.away}
                  note={attackSourceNote(preview.attack_sources.away)}
                />
              </AttackSourceSection>
            </>
          }
          players={
            <>
              <KeyPlayersSection
                homeName={homeName}
                awayName={awayName}
                homeBlocks={preview.key_players.home}
                awayBlocks={preview.key_players.away}
              />
              <GoalkeeperSection
                teams={[
                  { teamName: homeName, keepers: preview.keepers.home },
                  { teamName: awayName, keepers: preview.keepers.away },
                ]}
              />
            </>
          }
          shots={
            shotMapSpec ? (
              <figure className={styles.chartCard}>
                <ChartWithSummary
                  spec={shotMapSpec}
                  titleClassName={styles.chartTitle}
                  summaryClassName={styles.chartSummary}
                />
              </figure>
            ) : (
              <p className={styles.emptyText}>两队近期都没有可用的射门数据。</p>
            )
          }
        />
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
                {(() => {
                  const day = matchDayZh(m.kickoff_at_utc, m.date_utc);
                  return (
                    <>
                      {m.season} 赛季 · 比赛日 {day.text}
                      {day.isBeijing
                        ? "(按北京时间的开球日计)"
                        : "(来源只提供了比赛日期、没有开球时刻,此处按 UTC 自然日显示,不折算北京时间)"}
                    </>
                  );
                })()}
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
  preview,
  finished,
}: {
  idNum: number;
  detail: MatchDetailResponse;
  analysis: AnalysisBundle | null;
  preview: MatchPreviewResponse | null;
  finished: boolean;
}) {
  return (
    <>
      <HighlightsGroup idNum={idNum} detail={detail} finished={finished} />
      <DataGroup detail={detail} preview={preview} analysis={analysis} />
      <OddsGroup idNum={idNum} detail={detail} analysis={analysis} finished={finished} />
    </>
  );
}

export function MatchDetailBody({
  idNum,
  detail,
  analysis,
  report = null,
  preview = null,
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
  /** /matches/{id}/preview(阵容/伤停快照 + 风格 + 球员):数据 tab 阵容/风格/球员三个子 tab 的数据源 */
  preview?: MatchPreviewResponse | null;
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
          {/* 已完赛比赛不该把用户送回"未来七天赛程"——那是另一个语境。
              指向赛果视图,用户能接着看别的已完赛比赛。 */}
          <Link
            href={finished ? "/matches?status=finished" : "/matches?status=upcoming&window=7d"}
            className={styles.contextNavWide}
          >
            {finished ? "查看更多赛果" : "查看本周其他比赛"}
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
            <OverviewPanel
              idNum={idNum}
              detail={detail}
              analysis={analysis}
              preview={preview}
              finished={finished}
            />
          }
          shots={
            <MatchShotsSection
              shots={factReport.shots}
              momentum={factReport.momentum}
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
              teamStatsByHalf={factReport.team_stats_by_half}
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
          data={<DataGroup detail={detail} preview={preview} analysis={analysis} />}
          odds={<OddsGroup idNum={idNum} detail={detail} analysis={analysis} finished={finished} />}
        />
      )}
    </main>
  );
}
