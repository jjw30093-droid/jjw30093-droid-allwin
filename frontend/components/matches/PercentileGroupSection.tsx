/**
 * 联赛百分位画像:攻/守/控三组通用渲染(2026-09 重构)。
 *
 * 替换原来三个"按 max(主,客) 归一化"的组件(AttackChainSection/
 * PossessionControlSection/DefensivePressureSection)——那种条长只编码两队
 * 比值,每行比例尺不同,跨行无法扫读,联赛参照物完全不可见。这里改成
 * 所有行共用同一条 0~100 联赛百分位轴:0=垫底、50=联赛中游(均值刻度)、
 * 100=联赛第一,谁的点更靠右就是谁更强,点间距就是"强多少"。
 *
 * **用 CSS 定位实现,不用 ECharts**:这是一根固定 0~100 轴 + 两个点,没有
 * 坐标变换、没有自适应刻度、没有碰撞避让——ECharts 在这里是重锤。代价是
 * 不受 `EChart` 封装的双层错误边界与 SSR 渲染冒烟测试范式保护,因此这里
 * 的定位算术全部收在 `matchProfile.ts` 纯函数里并有测试覆盖
 * (`percentile-group-section.test.tsx`),配色另有独立的合成对比度测试
 * (`percentile-contrast.test.ts`)。
 *
 * 2026-09 第三轮(站长以 30-45 岁手机用户视角复看后的可读性修复):
 * - 数值行原来是"队名+数值+百分位"横排四元素 ×2,375px 实测 54px 高、
 *   已经换行(「利物浦」被拆成「利物/浦」),且两队同字号同粗同色——
 *   零强调,站长原话"我需要自己去看数字是多少,多了多少,少了多少"。
 *   现改为左右分置的双层块(照搬 MatchupSection 的 .rowSide 范式:
 *   18px 大数在上、12px 小注在下),归属靠队徽不靠位置,队名不再逐行重复
 *   (一屏内曾出现 78 次)。
 * - 新增差值胶囊:队名(谁领先)+ 箭头(原始值更多/更少)+ 数字(差多少)
 *   三通道冗余,沿用 OddsTimeline 的 data-dir 写法(CLAUDE.md §11.2)。
 *   领先方由**百分位**判定——百分位在后端已按 direction 归一化,
 *   xGA 这类"越低越好"的指标不需要前端再翻转一次。
 * - 每组默认只展开差距 ≥ GAP_FLOOR 的项(上限 4),其余进 <details>。
 * - 「联赛样本 N 队」从每行移到卡片头一次,轴刻度只在首行标一次。
 *
 * 诚实纪律:`percentile == null`(联赛分布不足 5 队,或该队本赛季同主客场
 * 场次不足)时该侧不画点,显示"暂无数据",不画在 0 或 50 这类会被误读成
 * 真实测量值的位置;差值任一侧缺值时整个胶囊不渲染,不画 0。
 */

"use client";

import pageStyles from "@/app/matches/[matchId]/match-detail.module.css";
import { TeamBadge } from "@/components/teams/TeamBadge";
import { CrestDot } from "./CrestDot";
import styles from "./PercentileGroupSection.module.css";
import {
  DEFAULT_MODE,
  decimalsFor,
  formatMetricValue,
  groupVerdict,
  metricGap,
  splitMetricsByGap,
  valueDelta,
  type GroupProfile,
  type MetricProfile,
  type ProfileMode,
  type ValueDelta,
} from "./matchProfile";

/** 差值胶囊:队名 + 箭头 + 幅度三通道。颜色用领先方队色(语义是"哪一队",
 * 不是涨跌/对错,所以不挪用 --odds-up/down 或 --win/--loss)。 */
function DeltaChip({ delta }: { delta: ValueDelta }) {
  const arrow = delta.dir === "up" ? "↑" : "↓";
  return (
    <span
      className={styles.deltaChip}
      data-side={delta.leader}
      aria-label={`${delta.leaderName}${delta.dir === "up" ? "多" : "少"} ${delta.magnitude}`}
    >
      <span aria-hidden>
        {delta.leaderName}
        <span className={`${styles.deltaNum} num`}>
          {arrow}
          {delta.magnitude}
        </span>
      </span>
    </span>
  );
}

function SideValue({
  metric,
  side,
  teamName,
  crestUrl,
  isLeader,
  mode,
}: {
  metric: MetricProfile;
  side: "home" | "away";
  teamName: string;
  crestUrl?: string | null;
  isLeader: boolean;
  mode: ProfileMode;
}) {
  const digits = decimalsFor(metric.unit);
  const value = side === "home" ? metric.home_value : metric.away_value;
  const pct = side === "home" ? metric.home_percentile : metric.away_percentile;
  const complete = side === "home" ? metric.home_complete : metric.away_complete;
  const showPct = mode !== "cross_league_raw";
  const partial = value != null && !complete;
  return (
    <span
      className={styles.side}
      data-side={side}
      // 跨联赛模式不给"领先方"高亮:那层队色加粗读作"这队更好",而两队分处
      // 不同联赛,这个判断站不住脚(站长明确要求不下强弱结论)。
      data-leader={isLeader && showPct ? "true" : undefined}
    >
      <TeamBadge teamName={teamName} crestUrl={crestUrl} size={24} />
      <span className={styles.sideText}>
        <b className={`${styles.sideValue} num`}>
          {formatMetricValue(value ?? null, metric.unit, digits)}
        </b>
        {(showPct || partial) && (
          <span className={styles.sidePct}>
            {/* 跨联赛模式一个分位都没有,每行印一遍"暂无分位"只是噪声 */}
            {showPct && (pct != null ? `第 ${pct} 分位` : "暂无分位")}
            {partial && <sup className={styles.partial}>*</sup>}
          </span>
        )}
      </span>
    </span>
  );
}

function MetricRow({
  metric,
  homeName,
  awayName,
  homeCrestUrl,
  awayCrestUrl,
  showAxisScale,
  mode,
}: {
  metric: MetricProfile;
  homeName: string;
  awayName: string;
  homeCrestUrl?: string | null;
  awayCrestUrl?: string | null;
  /** 轴刻度「垫底/联赛中游/第一」只在每组首行标一次——原来每行都标,
   * 一屏内重复 12 次。 */
  showAxisScale: boolean;
  mode: ProfileMode;
}) {
  const digits = decimalsFor(metric.unit);
  const noData = metric.home_value == null && metric.away_value == null;
  if (noData) {
    return (
      <div className={styles.row}>
        <div className={styles.rowHead}>
          <span className={styles.label}>{metric.name_zh}</span>
        </div>
        <p className={styles.emptyRow}>两队近期同主客场比赛都无该项数据。</p>
      </div>
    );
  }
  // 跨联赛模式没有百分位轴可画——画一根空轴比不画更让人困惑(轴在,点没有)。
  const showAxis = mode !== "cross_league_raw";
  const { leader } = metricGap(metric, mode);
  const delta = valueDelta(metric, homeName, awayName, mode);
  return (
    <div className={styles.row}>
      <div className={styles.rowHead}>
        <span className={styles.label}>{metric.name_zh}</span>
        {delta && <DeltaChip delta={delta} />}
      </div>
      {showAxis && (
        <div
          className={styles.axis}
          role="img"
          aria-label={`${metric.name_zh}:${homeName} ${formatMetricValue(metric.home_value ?? null, metric.unit, digits)}${
            metric.home_percentile != null ? `,联赛第 ${metric.home_percentile} 百分位` : ",暂无联赛百分位"
          };${awayName} ${formatMetricValue(metric.away_value ?? null, metric.unit, digits)}${
            metric.away_percentile != null ? `,联赛第 ${metric.away_percentile} 百分位` : ",暂无联赛百分位"
          }。`}
        >
          <span className={styles.meanTick} aria-hidden />
          {metric.home_percentile != null && (
            <CrestDot pct={metric.home_percentile} crestUrl={homeCrestUrl} teamName={homeName} side="home" />
          )}
          {metric.away_percentile != null && (
            <CrestDot pct={metric.away_percentile} crestUrl={awayCrestUrl} teamName={awayName} side="away" />
          )}
        </div>
      )}
      {showAxis && showAxisScale && (
        <div className={styles.axisLabels}>
          <span>垫底</span>
          <span>联赛中游</span>
          <span>第一</span>
        </div>
      )}
      <div className={styles.valuesRow}>
        <SideValue
          metric={metric}
          side="home"
          teamName={homeName}
          crestUrl={homeCrestUrl}
          isLeader={leader === "home"}
          mode={mode}
        />
        <SideValue
          metric={metric}
          side="away"
          teamName={awayName}
          crestUrl={awayCrestUrl}
          isLeader={leader === "away"}
          mode={mode}
        />
      </div>
    </div>
  );
}

export function PercentileGroupSection({
  title,
  windowNote,
  homeName,
  awayName,
  homeCrestUrl,
  awayCrestUrl,
  group,
  /** 口径说明只在最后一个百分位模块底部出现一次——三段几乎相同的说明
   * 原来每个模块各印一遍(「两套独立分布…」全页出现 4 次)。 */
  showMethodNote = false,
  mode = DEFAULT_MODE,
}: {
  title: string;
  windowNote: string;
  homeName: string;
  awayName: string;
  homeCrestUrl?: string | null;
  awayCrestUrl?: string | null;
  group: GroupProfile;
  showMethodNote?: boolean;
  mode?: ProfileMode;
}) {
  const semantic = group.metrics[0]?.semantic ?? "performance";
  const { ordered, visibleCount } = splitMetricsByGap(group.metrics, mode);
  const visible = ordered.slice(0, visibleCount);
  const hidden = ordered.slice(visibleCount);
  const sampleSize = group.metrics.find((m) => m.league_sample_size > 0)?.league_sample_size ?? 0;
  // 跨联赛模式下为空串——那句免责只在总览说一次,不在三张卡片各印一遍。
  const verdict = groupVerdict(group, semantic, homeName, awayName, mode);

  const renderRow = (m: MetricProfile, index: number) => (
    <MetricRow
      key={m.key}
      metric={m}
      homeName={homeName}
      awayName={awayName}
      homeCrestUrl={homeCrestUrl}
      awayCrestUrl={awayCrestUrl}
      showAxisScale={index === 0}
      mode={mode}
    />
  );

  return (
    <section className={pageStyles.section}>
      <h2 className={pageStyles.sectionTitle}>
        <span className={pageStyles.sectionBar} aria-hidden />
        {title}
      </h2>
      <p className={styles.windowNote}>{windowNote}</p>
      <div className={styles.card}>
        {(verdict || sampleSize > 0) && (
          <div className={styles.cardHead}>
            {verdict && <span className={styles.verdict}>{verdict}</span>}
            {sampleSize > 0 && <span className={styles.sampleNote}>联赛样本 {sampleSize} 队</span>}
          </div>
        )}
        {visible.map(renderRow)}
        {hidden.length > 0 && (
          <details className={styles.moreDetail}>
            <summary className={styles.moreSummary}>展开其余 {hidden.length} 项</summary>
            {hidden.map((m, i) => renderRow(m, visibleCount + i))}
          </details>
        )}
        {showMethodNote && (
          <details className={styles.methodDetail}>
            <summary className={styles.methodSummary}>口径说明</summary>
            <p className={styles.footNote}>
              {mode === "cross_league_raw" ? (
                // scopeNote 已经在总览卡片里说过一次,这里不重复,只解释 * 标记。
                <>
                  带 * 的数值表示该窗口内有场次缺该字段,均值只计入有数据的场次,不是全部窗口的合计。
                </>
              ) : (
                <>
                  横轴是本联赛同场景分布里的百分位,不是两队互相比较的比值。主队对联赛主场分布取百分位,
                  客队对联赛客场分布取百分位——两套独立分布,不是同一把绝对尺子。带 * 的数值表示该场景窗口内
                  有场次缺该字段,均值只计入有数据的场次,不是全部窗口的合计。
                </>
              )}
            </p>
          </details>
        )}
      </div>
    </section>
  );
}
