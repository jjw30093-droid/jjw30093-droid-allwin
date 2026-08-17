/**
 * 图1 · 进攻转化链(PREMATCH_MOBILE_DATA_VISUALIZATION_V2 Phase 2.1,
 * 验收返工一:补真实转化率语义)。
 *
 * 明确分两组展示,不堆成一串同质数字:
 * - **进攻产量**:进攻半场传球占比 → 禁区触球 → 射门 → 射正 → xG → xGOT,
 *   各自独立的窗口场均值。
 * - **转化效率**:每 100 次禁区触球出多少射门、射正率、每脚射门 xG、
 *   每次射正 xGOT——每一项都是同一场比赛内两个字段的配对相除,不是两个
 *   独立均值相除(见 `backend/queries/attack_chain.py`)。
 *
 * 主队(teal 色条)在上、客队(蓝色条)在下,不用桑基图(手机端窄屏画不清楚
 * 流量分叉)。
 *
 * 诚实纪律:某环节两队都没有数据时该行整体显示「数据不足」,不画 0 宽度的
 * 条(那看起来像"真实测出来是 0");`complete=false` 时该值仍然展示(用
 * 有数据的场次算出来的均值本身没错),但标一个星号 + 底部脚注说明这是
 * 部分场次的均值,不是全窗口。
 */

"use client";

import styles from "./AttackChainSection.module.css";
import pageStyles from "@/app/matches/[matchId]/match-detail.module.css";
import { INCOMPARABLE_NOTE, tiersComparable } from "./comparability";
import type { components } from "@/lib/api-types";

type AttackChain = components["schemas"]["MatchPreviewAttackChainDTO"];
type ChainMetric = components["schemas"]["MatchPreviewChainMetricDTO"];

const VOLUME_STAGES: { key: keyof AttackChain & string; label: string; unit: string; decimals: number }[] = [
  { key: "opp_half_pass_share", label: "进攻半场传球占比", unit: "%", decimals: 1 },
  { key: "touches_opp_box", label: "禁区触球", unit: "次/场", decimals: 1 },
  { key: "shots", label: "射门", unit: "次/场", decimals: 1 },
  { key: "shots_on_target", label: "射正", unit: "次/场", decimals: 1 },
  { key: "xg", label: "xG", unit: "/场", decimals: 2 },
  { key: "xgot", label: "xGOT", unit: "/场", decimals: 2 },
];

const CONVERSION_STAGES: { key: keyof AttackChain & string; label: string; unit: string; decimals: number }[] = [
  { key: "shots_per_100_box_touches", label: "每 100 次禁区触球出射门", unit: "次", decimals: 1 },
  { key: "shot_on_target_rate", label: "射正率", unit: "%", decimals: 1 },
  { key: "xg_per_shot", label: "每脚射门 xG", unit: "", decimals: 3 },
  { key: "xgot_per_sot", label: "每次射正 xGOT", unit: "", decimals: 3 },
];

function metricOf(chain: AttackChain, key: string): ChainMetric {
  return (chain as unknown as Record<string, ChainMetric>)[key];
}

function StageRow({
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
              <span
                className={styles.stageFillHome}
                style={{ width: `${(home.value / max) * 100}%` }}
              />
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
              <span
                className={styles.stageFillAway}
                style={{ width: `${(away.value / max) * 100}%` }}
              />
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

function chainSummary(homeName: string, awayName: string, home: AttackChain, away: AttackChain): string {
  // 验收返工三:mixed(样本不足退回合并主客场)跟 venue_full/venue_partial
  // (纯同场景窗口)口径不一样,不生成"谁更高"这类比较结论——原始数字仍
  // 照常展示,只是不下比较结论。
  if (!tiersComparable(home.tier, away.tier)) return INCOMPARABLE_NOTE;
  let bestKey: string | null = null;
  let bestGap = 0;
  let bestLabel = "";
  for (const s of [...VOLUME_STAGES, ...CONVERSION_STAGES]) {
    const h = metricOf(home, s.key).value;
    const a = metricOf(away, s.key).value;
    if (h == null || a == null) continue;
    const base = Math.max(Math.abs(h), Math.abs(a), 0.01);
    const gap = Math.abs(h - a) / base;
    if (gap > bestGap) {
      bestGap = gap;
      bestKey = s.key;
      bestLabel = s.label;
    }
  }
  if (!bestKey) return "两队近期同主客场比赛可比数据不足,暂无法给出转化链对比。";
  const h = metricOf(home, bestKey).value as number;
  const a = metricOf(away, bestKey).value as number;
  const leader = h >= a ? homeName : awayName;
  return `两队在「${bestLabel}」这一项差异最明显,${leader}的近期同场景均值更高。`;
}

export function AttackChainSection({
  homeName,
  awayName,
  home,
  away,
}: {
  homeName: string;
  awayName: string;
  home: AttackChain;
  away: AttackChain;
}) {
  return (
    <section className={pageStyles.section}>
      <h2 className={pageStyles.sectionTitle}>
        <span className={pageStyles.sectionBar} aria-hidden />
        进攻转化链
      </h2>
      <p className={styles.windowNote}>
        {homeName}({home.label_zh})上方色条 · {awayName}({away.label_zh})下方色条 ——
        进攻产量看两队各自打出多少,转化效率看这些产量有没有真正变成射正和进球威胁。
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
        <h3 className={styles.groupTitle}>进攻产量</h3>
        <div className={styles.stages}>
          {VOLUME_STAGES.map((s) => (
            <StageRow
              key={s.key}
              label={s.label}
              unit={s.unit}
              decimals={s.decimals}
              home={metricOf(home, s.key)}
              away={metricOf(away, s.key)}
            />
          ))}
        </div>
        <h3 className={styles.groupTitle}>转化效率</h3>
        <div className={styles.stages}>
          {CONVERSION_STAGES.map((s) => (
            <StageRow
              key={s.key}
              label={s.label}
              unit={s.unit}
              decimals={s.decimals}
              home={metricOf(home, s.key)}
              away={metricOf(away, s.key)}
            />
          ))}
        </div>
        <p className={styles.summary}>{chainSummary(homeName, awayName, home, away)}</p>
        <p className={styles.footNote}>* 该场景窗口内有场次缺该字段,均值只计入有数据的场次,不是全部窗口的合计。</p>
      </div>
    </section>
  );
}
