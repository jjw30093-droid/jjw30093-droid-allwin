/**
 * 图2 · 控球与场面控制(PREMATCH_MOBILE_DATA_VISUALIZATION_V2 Phase 2.2)。
 *
 * 回答「有控球是不是真能推进到威胁区,还是空转」——控球率 · 传球成功率 ·
 * 进攻半场传球占比 · 禁区触球,四条配对横条。视觉复用 AttackChainSection
 * 的样式(同一套 home=teal/away=blue 配对条形语言,不重复定义一遍 CSS)。
 */

"use client";

import styles from "./AttackChainSection.module.css";
import pageStyles from "@/app/matches/[matchId]/match-detail.module.css";
import { INCOMPARABLE_NOTE, tiersComparable } from "./comparability";
import type { components } from "@/lib/api-types";

type PossessionControl = components["schemas"]["MatchPreviewPossessionControlDTO"];
type ChainMetric = components["schemas"]["MatchPreviewChainMetricDTO"];

const ROWS: { key: keyof PossessionControl & string; label: string; unit: string; decimals: number }[] = [
  { key: "possession", label: "控球率", unit: "%", decimals: 1 },
  { key: "pass_accuracy", label: "传球成功率", unit: "%", decimals: 1 },
  { key: "opp_half_pass_share", label: "进攻半场传球占比", unit: "%", decimals: 1 },
  { key: "touches_opp_box", label: "禁区触球", unit: "次/场", decimals: 1 },
];

function metricOf(c: PossessionControl, key: string): ChainMetric {
  return (c as unknown as Record<string, ChainMetric>)[key];
}

function ControlRow({
  label,
  unit,
  decimals,
  home,
  away,
}: {
  label: string;
  unit: string;
  decimals: number;
  home: ChainMetric;
  away: ChainMetric;
}) {
  if (home.value == null && away.value == null) {
    return (
      <div className={styles.stageRow}>
        <span className={styles.stageLabel}>{label}</span>
        <span className={styles.stageEmpty}>两队近期同主客场比赛都无该项数据</span>
      </div>
    );
  }
  const max = Math.max(home.value ?? 0, away.value ?? 0) || 1;
  const fmt = (m: ChainMetric) => (m.value == null ? "数据不足" : `${m.value.toFixed(decimals)}${unit}`);
  return (
    <div className={styles.stageRow}>
      <span className={styles.stageLabel}>{label}</span>
      <div className={styles.stageBars}>
        <div className={styles.stageBarLine}>
          <span className={styles.stageTrack}>
            {home.value != null && (
              <span className={styles.stageFillHome} style={{ width: `${(home.value / max) * 100}%` }} />
            )}
          </span>
          <span className={`${styles.stageValue} num`}>
            {fmt(home)}
            {home.value != null && !home.complete && <sup className={styles.partial}>*</sup>}
          </span>
        </div>
        <div className={styles.stageBarLine}>
          <span className={styles.stageTrack}>
            {away.value != null && (
              <span className={styles.stageFillAway} style={{ width: `${(away.value / max) * 100}%` }} />
            )}
          </span>
          <span className={`${styles.stageValue} num`}>
            {fmt(away)}
            {away.value != null && !away.complete && <sup className={styles.partial}>*</sup>}
          </span>
        </div>
      </div>
    </div>
  );
}

function controlSummary(homeName: string, awayName: string, home: PossessionControl, away: PossessionControl): string {
  if (!tiersComparable(home.tier, away.tier)) return INCOMPARABLE_NOTE;
  const h = home.possession.value;
  const a = away.possession.value;
  const hBox = home.touches_opp_box.value;
  const aBox = away.touches_opp_box.value;
  if (h == null || a == null || hBox == null || aBox == null) {
    return "两队近期同主客场比赛可比数据不足,暂无法给出控球对比。";
  }
  const possessionLeader = h >= a ? homeName : awayName;
  const boxLeader = hBox >= aBox ? homeName : awayName;
  if (possessionLeader === boxLeader) {
    return `${possessionLeader}控球率更高,禁区触球也更多——控球更多地转化成了推进。`;
  }
  return `${possessionLeader}控球率更高,但禁区触球更多的是${boxLeader}——控球和推进不完全同步。`;
}

export function PossessionControlSection({
  homeName,
  awayName,
  home,
  away,
}: {
  homeName: string;
  awayName: string;
  home: PossessionControl;
  away: PossessionControl;
}) {
  return (
    <section className={pageStyles.section}>
      <h2 className={pageStyles.sectionTitle}>
        <span className={pageStyles.sectionBar} aria-hidden />
        控球与场面控制
      </h2>
      <p className={styles.windowNote}>
        {homeName}({home.label_zh})上方色条 · {awayName}({away.label_zh})下方色条 ——
        有控球不代表能推进到危险区,对比控球率和禁区触球能看出差别。
      </p>
      <div className={styles.card}>
        <div className={styles.legendRow}>
          <span className={styles.legendItem}>
            <span className={styles.swatchHome} aria-hidden />
            {homeName}
          </span>
          <span className={styles.legendItem}>
            <span className={styles.swatchAway} aria-hidden />
            {awayName}
          </span>
        </div>
        <div className={styles.stages}>
          {ROWS.map((r) => (
            <ControlRow
              key={r.key}
              label={r.label}
              unit={r.unit}
              decimals={r.decimals}
              home={metricOf(home, r.key)}
              away={metricOf(away, r.key)}
            />
          ))}
        </div>
        <p className={styles.summary}>{controlSummary(homeName, awayName, home, away)}</p>
        <p className={styles.footNote}>
          进攻半场传球占比是本站用现有数据算的代理指标,不是 Opta/StatsBomb 的官方 Field Tilt。
          * 该场景窗口内有场次缺该字段,均值只计入有数据的场次,不是全部窗口的合计。
        </p>
      </div>
    </section>
  );
}
