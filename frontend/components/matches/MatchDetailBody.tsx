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
import type {
  GetJson,
  MatchDetailResponse,
  MatchReportResponse,
  MatchSummary,
} from "@/lib/api-v1";
import { FollowButton } from "@/components/matches/FollowButton";
import { RecordVisit } from "@/components/matches/RecordVisit";
import { OddsTimeline } from "@/components/matches/OddsTimeline";
import { CooccurrenceSection } from "@/components/matches/CooccurrenceSection";
import { MarketCardsSection } from "@/components/matches/MarketCardsSection";
import { LocalTime } from "@/components/matches/LocalTime";
import { ChartWithSummary } from "@/components/matches/ChartWithSummary";
import { TeamBadge } from "@/components/teams/TeamBadge";
import { MatchTabs } from "@/components/matches/MatchTabs";
import { MatchLineupSection } from "@/components/matches/MatchLineupSection";
import { MatchStatsSection } from "@/components/matches/MatchStatsSection";
import { MatchShotsSection } from "@/components/matches/MatchShotsSection";
import { MatchEventsSection } from "@/components/matches/MatchEventsSection";
import { LEAGUE_ZH, STATUS_ZH, formatDateZh } from "@/components/matches/zh";
import { syncStateLabel } from "@/lib/product-status";
import styles from "@/app/matches/[matchId]/match-detail.module.css";

export type AnalysisBundle = GetJson<"/api/v1/matches/{match_id}/analysis">;
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
 * 训练前不可用,详情页不渲染任何概率(用户拍板,见比赛详情页重设计
 * 计划 §四②)——结论改为基于近期战绩现算的一句真实数据对比,双方均无
 * 数据时如实收窄,不铺"暂无正式预测"这类模型语言。
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

      <footer className={styles.quickFoot}>
        <a href="#match-data" className={styles.quickMore}>
          查看详细数据 ↓
        </a>
      </footer>
    </section>
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
  /** /matches/{id}/report(完赛事实):available 时渲染 总览/阵容/统计/事件 四 tab */
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
            <Link href={`/matches/${previousMatch.match_id}`}>同联赛上一场</Link>
          )}
          {nextMatch && <Link href={`/matches/${nextMatch.match_id}`}>同联赛下一场</Link>}
          <Link href="/matches?status=upcoming&window=7d">查看本周其他比赛</Link>
        </span>
      </nav>
      {/* 1. 比赛头部 */}
      <header className={styles.header}>
        {/* 标签压缩(不做胶囊标签墙):联赛·轮次一枚 + 状态;赛季属第二层
            信息,移到下方日期行。 */}
        <div className={styles.metaRow}>
          <span className={styles.chip}>
            {LEAGUE_ZH[m.league_id] ?? `联赛 ${m.league_id}`}
            {m.round ? ` · 第 ${m.round} 轮` : ""}
          </span>
          <span className={styles.chipStatus}>{STATUS_ZH[m.status] ?? m.status}</span>
          {/* 只保留可行动的 STALE 提示;"部分数据暂不可用"这类统一模糊标签
              信息噪声大又不说明缺什么,不再逐场展示(数据溯源仍在
              下方「数据来源与说明」与后台)。 */}
          {m.sync_state === "STALE" && (
            <span className={`${styles.chipStatus} ${styles.chipStale}`}>
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
        <div className={styles.followRow}>
          <FollowButton matchId={idNum} />
        </div>
        <p className={styles.dateLine}>
          {m.season} 赛季 · 比赛日:{formatDateZh(m.date_utc)}
          {m.kickoff_at_utc && (
            <>
              {" · 开球:"}
              <LocalTime iso={m.kickoff_at_utc} />
            </>
          )}
          {detail.data_updated_at && (
            <>
              {" · 数据更新于 "}
              <LocalTime iso={detail.data_updated_at} />
            </>
          )}
        </p>
        {/* 采集轮询计划、观测点数等内部审计信息不进首屏
            (下方「数据来源与说明」与后台仍可溯源)。模型概率未经真实训练前
            不可用,详情页不再渲染概率或其来源说明。 */}
        {m.sync_state === "STALE" && (
          <p className={styles.staleNotice}>
            数据已过期；
            {m.next_planned_sync_at ? "正在等待计划采集。" : "正在等待采集恢复。"}
          </p>
        )}
      </header>

      {factReport ? (
        /* 完赛且事实报告可用:总览/阵容/统计/事件 四 tab。
           总览 = §11.1 的 6 段原样;无报告路径在下方 else 分支平铺同一内容。 */
        <MatchTabs
          shots={
            <MatchShotsSection
              shots={factReport.shots}
              homeName={m.home.name}
              awayName={m.away.name}
              homeScore={m.home_score}
              awayScore={m.away_score}
            />
          }
          overview={
            <OverviewSections
              idNum={idNum}
              detail={detail}
              analysis={analysis}
              finished={finished}
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
        <OverviewSections
          idNum={idNum}
          detail={detail}
          analysis={analysis}
          finished={finished}
        />
      )}
    </main>
  );
}

/**
 * 固定顺序的 5 段(2 本场看点 → 3 证据 → 4 可视化 → 5 赔率时间轴 →
 * 6 同期事件 → 7 模型与登记信息)。有完赛事实报告时作为「总览」tab 的内容,
 * 否则直接平铺。模型概率区块已下架(见 QuickView 顶部说明)。
 */
function OverviewSections({
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
      {/* 2. 本场看点:先结论后证据(赛前决策入口;完赛后事实报告本身
          就是结论,不再渲染本场看点) */}
      {!finished && <QuickView detail={detail} />}

      {/* 2.5 市场卡:数据倾向 + 折叠归因(赛前之墙唯一能给"这场比赛特有"
          内容的地方——赛后事实表对未开赛比赛精确为 0 行,只有两队历史聚合
          可用)。完赛后同样展示,归因区解释的是历史规律,不因完赛而失效。 */}
      <section className={styles.section} id="market-cards">
        <h2 className={styles.sectionTitle}>数据倾向</h2>
        <MarketCardsSection matchId={idNum} />
      </section>

      {/*
        原第 3 块「证据与不确定性」已整体下架(2026-08-12)。

        它不是"太复杂看不懂",而是在输出错误信息 —— 实跑 200 场未来比赛:
        · 生成逻辑是 backend/studio/bundle.py:153-232 一串硬编码 if/else,不是数据;
        · 最高频的 evidence 是 rest(休息天数),因为 _rest_days 只查上一场完赛、
          不区分休赛期,中位数 24 天、**最大 1917.8 天**;
        · uncertainty 100% 的比赛显示"暂无赛前特征数据",80% 显示"未以 0 填充"
          (给工程师看的内部口径);
        · **84/200 (42%) 场次 evidence 为空**,页面上是两个虚线空框;
        · counter_evidence 的隐含命题("主胜")从未写出来,导致
          "XX 近5场3胜1平1负,不容小视"顶着「反向证据」标题出现。

        bundle 的三个字段保留(Studio 的 script_sections.risk 有软依赖),只下架
        展示面。真实数据驱动的可视化模块见同级「数据可视化」区块。
      */}

      {/* 3. 可视化:近期表现 + chart_specs */}
      <section className={styles.section} id="match-data">
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

      {/* 6. 关键变化(时间共现,不声称因果;无内容时组件整体不渲染,
             区块标题由组件自带,避免空标题占屏) */}
      <CooccurrenceSection matchId={idNum} />

      {/* 7. 模型版本 / cutoff / 局限 / 登记信息 —— 默认折叠:属数据溯源
             说明而非比赛结论,展开后内容一字不少。 */}
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
