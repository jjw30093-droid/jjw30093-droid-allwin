/**
 * 图3 · 防守承压与限制能力(PREMATCH_MOBILE_DATA_VISUALIZATION_V2 Phase 2.3)。
 *
 * 回答「这支球队让对手打出了多少威胁,限制能力怎么样」——被射门 · 被射正 ·
 * xGA(让出 xG) · 禁区内被射门,四项全部来自对手在同一场比赛的数值。
 *
 * 诚实纪律:这里只展示防守结果/限制能力,不展示拦截/解围/封堵这类防守
 * 动作或风格——避免"解围多=防守强"的误导(方案 §三·图3 明确禁止)。
 */

"use client";

import styles from "./AttackChainSection.module.css";
import pageStyles from "@/app/matches/[matchId]/match-detail.module.css";
import { INCOMPARABLE_NOTE, tiersComparable } from "./comparability";
import type { components } from "@/lib/api-types";

type DefensivePressure = components["schemas"]["MatchPreviewDefensivePressureDTO"];
type ChainMetric = components["schemas"]["MatchPreviewChainMetricDTO"];

const ROWS: { key: keyof DefensivePressure & string; label: string; unit: string; decimals: number }[] = [
  { key: "shots_faced", label: "被射门", unit: "次/场", decimals: 1 },
  { key: "shots_on_target_faced", label: "被射正", unit: "次/场", decimals: 1 },
  { key: "xga", label: "预期失球(xGA)", unit: "/场", decimals: 2 },
  { key: "box_shots_faced", label: "禁区内被射门", unit: "次/场", decimals: 1 },
];

function metricOf(c: DefensivePressure, key: string): ChainMetric {
  return (c as unknown as Record<string, ChainMetric>)[key];
}

function PressureRow({
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

function pressureSummary(
  homeName: string,
  awayName: string,
  home: DefensivePressure,
  away: DefensivePressure,
): string {
  if (!tiersComparable(home.tier, away.tier)) return INCOMPARABLE_NOTE;
  const h = home.xga.value;
  const a = away.xga.value;
  if (h == null || a == null) {
    return "两队近期同主客场比赛可比数据不足,暂无法给出防守承压对比。";
  }
  const tighter = h <= a ? homeName : awayName;
  const looser = tighter === homeName ? awayName : homeName;
  return `按 xGA 看,${tighter}近期让对手打出的期望进球更少,${looser}承压更明显。`;
}

export function DefensivePressureSection({
  homeName,
  awayName,
  home,
  away,
}: {
  homeName: string;
  awayName: string;
  home: DefensivePressure;
  away: DefensivePressure;
}) {
  return (
    <section className={pageStyles.section}>
      <h2 className={pageStyles.sectionTitle}>
        <span className={pageStyles.sectionBar} aria-hidden />
        防守承压与限制能力
      </h2>
      <p className={styles.windowNote}>
        {homeName}({home.label_zh})上方色条 · {awayName}({away.label_zh})下方色条 ——
        数字是对手在这些比赛里打出的量,越低说明限制能力越强。预期失球(xGA)是按对手每次
        射门的进球概率估出来的「应该丢几个球」,不是实际失球数。
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
            <PressureRow
              key={r.key}
              label={r.label}
              unit={r.unit}
              decimals={r.decimals}
              home={metricOf(home, r.key)}
              away={metricOf(away, r.key)}
            />
          ))}
        </div>
        <p className={styles.summary}>{pressureSummary(homeName, awayName, home, away)}</p>
        <p className={styles.footNote}>
          这里只统计被射门/被射正/xGA/禁区内被射门这些防守结果,不包含拦截、解围、封堵——
          这些是防守动作或风格,动作次数多不等于防守能力强。
          * 该场景窗口内有场次缺该字段,均值只计入有数据的场次,不是全部窗口的合计。
        </p>
      </div>
    </section>
  );
}
