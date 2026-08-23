/**
 * 图4 · 本场攻防对位(PREMATCH_MOBILE_DATA_VISUALIZATION_V2 Phase 2.4,最高优先级)。
 *
 * 回答「这队最擅长的进攻方式,对手扛不扛得住」——运动战 / 反击 / 定位球
 * (含角球)/ 禁区内射门,交叉对比一方的进攻场均产出与另一方在同类型的
 * 场均失球压力。两个方向都展示(主队进攻 vs 客队防守、客队进攻 vs 主队
 * 防守),最多高亮两个真实数字支撑的关键对位,不合成 0-100 综合评分。
 */

"use client";

import styles from "./MatchupSection.module.css";
import pageStyles from "@/app/matches/[matchId]/match-detail.module.css";
import { INCOMPARABLE_NOTE, tiersComparable } from "./comparability";
import type { components } from "@/lib/api-types";

type MatchupProfile = components["schemas"]["MatchPreviewMatchupProfileDTO"];
type MatchupSituation = components["schemas"]["MatchPreviewMatchupSituationDTO"];

type Row = {
  attackerName: string;
  defenderName: string;
  situation: MatchupSituation;
  defConceded: MatchupSituation | undefined;
  /** 是否满足"关键对位"资格:进攻方该类型产出高于联赛/场景基准,
   * 且防守方该类型让出值也高于联赛/场景基准。缺基准或任一方未过线时
   * 不合格——不产生任何排序分数,不会被高亮。 */
  qualifies: boolean;
  /** 仅用于在多个合格候选之间挑最多两个:双方相对各自基准的超出比例之和
   * (量纲无关的相对值,不是原始 xG/次数),避免"运动战原始基数天然更大
   * 就永远排第一"(验收返工二)。不合格的候选没有意义,不参与比较。 */
  rankScore: number;
};

function findSituation(profile: MatchupProfile, key: string): MatchupSituation | undefined {
  return profile.situations.find((s) => s.key === key);
}

function buildRows(
  attackerName: string,
  attacker: MatchupProfile,
  defenderName: string,
  defender: MatchupProfile,
): Row[] {
  // 验收返工三:进攻方窗口(attacker.tier)和防守方窗口(defender.tier)
  // 口径不兼容(比如一方 mixed、一方 venue_full,或一方 venue_full、一方
  // venue_partial)时,这个方向整体不产生"关键对位"——两支队伍各自的
  // 窗口本来就是独立算出来的,不同 tier 不是同一回事,不能拿这个方向的
  // 数字互相印证。
  const directionComparable = tiersComparable(attacker.tier, defender.tier);
  return attacker.situations.map((situation) => {
    const defConceded = findSituation(defender, situation.key);
    // 验收返工二(P1):比较值/基准值全部是后端已经按 comparison_metric
    // (xg 或 shots)显式算好的字段,前端不再用 `own_xg_pg ?? own_shots_pg`
    // 猜量纲——那在 xG 不完整时会把射门次数当 xG 用去跟 xG 基准比。
    // 这里独立检查己方(进攻)侧和对手(防守)侧各自的四个值是否都在,
    // 不能直接借用后端 `comparison_complete`(那是同一支队自己
    // own+conceded 两侧合起来的完整性,跟这里"进攻方 own 配防守方
    // conceded"这个方向所需的完整性不是同一件事)。
    const ownReady = situation.own_comparison_value != null && situation.own_baseline_value != null;
    const concReady = defConceded?.conceded_comparison_value != null && defConceded?.conceded_baseline_value != null;
    const ownValue = situation.own_comparison_value;
    const concValue = defConceded?.conceded_comparison_value;
    const ownBaseline = situation.own_baseline_value;
    const concBaseline = defConceded?.conceded_baseline_value;
    const baselineReady =
      directionComparable && ownReady && concReady &&
      ownBaseline! > 0 && concBaseline! > 0;

    const qualifies = baselineReady && ownValue! > ownBaseline! && concValue! > concBaseline!;
    const rankScore = qualifies
      ? (ownValue! / ownBaseline! - 1) + (concValue! / concBaseline! - 1)
      : -Infinity;

    return { attackerName, defenderName, situation, defConceded, qualifies, rankScore };
  });
}

function fmtShots(v: number | null | undefined): string {
  return v == null ? "数据不足" : `${v.toFixed(1)} 次/场`;
}

// 2026-08-23 站长决定:某情境类型下只要有比赛缺 xG,就把那场整场剔除、
// 用剩下的干净场次重新算均值(不是直接判整类"数据不足")——干净场次数
// (sampleMatches)可能小于这一侧窗口的名义场次(windowMatches,比如
// "近 10 个客场"里其实只有 9 场贡献了这个数字)。不写出来会显得这个数字
// 和同一屏其它"近 10 场"口径一样,是静默改样本量——这里补一个"(近 N 场)"
// 让差异可见,不做无声的重新归一化。
function fmtXg(
  v: number | null | undefined,
  complete: boolean | undefined,
  sampleMatches: number | null | undefined,
  windowMatches: number | undefined,
): string {
  if (v == null) return complete === false ? "数据不足" : "—";
  const base = `xG ${v.toFixed(2)}/场`;
  if (sampleMatches != null && windowMatches != null && sampleMatches < windowMatches) {
    return `${base}(近 ${sampleMatches} 场)`;
  }
  return base;
}

function MatchupBlock({ attackerName, defenderName, attackerMatches, defenderMatches, rows, highlighted }: {
  attackerName: string;
  defenderName: string;
  attackerMatches: number;
  defenderMatches: number;
  rows: Row[];
  highlighted: Set<string>;
}) {
  return (
    <div className={styles.block}>
      <div className={styles.blockHead}>
        <span className={styles.attacker}>{attackerName} 进攻</span>
        <span className={styles.vs}>vs</span>
        <span className={styles.defender}>{defenderName} 防守</span>
      </div>
      <ul className={styles.rowList}>
        {rows.map((r) => {
          const key = `${attackerName}:${r.situation.key}`;
          const isHighlighted = highlighted.has(key);
          return (
            <li key={r.situation.key} className={isHighlighted ? styles.rowHighlighted : styles.row}>
              <span className={styles.rowLabel}>{r.situation.label}</span>
              <span className={styles.rowStats}>
                <span className={styles.rowSide}>
                  <b className="num">{fmtShots(r.situation.own_shots_pg)}</b>
                  <span className={styles.rowSideSub}>
                    {fmtXg(r.situation.own_xg_pg, r.situation.own_xg_complete, r.situation.own_xg_matches, attackerMatches)}
                  </span>
                </span>
                <span className={styles.rowArrow}>→</span>
                <span className={styles.rowSide}>
                  <b className="num">{fmtShots(r.defConceded?.conceded_shots_pg)}</b>
                  <span className={styles.rowSideSub}>
                    {fmtXg(
                      r.defConceded?.conceded_xg_pg,
                      r.defConceded?.conceded_xg_complete,
                      r.defConceded?.conceded_xg_matches,
                      defenderMatches,
                    )}
                  </span>
                </span>
              </span>
              {isHighlighted && <span className={styles.rowTag}>关键对位</span>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function MatchupSection({
  homeName,
  awayName,
  home,
  away,
}: {
  homeName: string;
  awayName: string;
  home: MatchupProfile;
  away: MatchupProfile;
}) {
  const homeAttack = buildRows(homeName, home, awayName, away);
  const awayAttack = buildRows(awayName, away, homeName, home);
  const qualifying = [...homeAttack, ...awayAttack].filter((r) => r.qualifies);
  const top2 = [...qualifying].sort((a, b) => b.rankScore - a.rankScore).slice(0, 2);
  const highlighted = new Set(top2.map((r) => `${r.attackerName}:${r.situation.key}`));

  // 验收返工三:tier 不兼容时不能用笼统的"可比数据不足"掩盖真正原因,
  // 必须明确说"样本口径不同"。
  const directionComparable = tiersComparable(home.tier, away.tier);
  const summary = !directionComparable
    ? INCOMPARABLE_NOTE
    : top2.length
      ? top2
          .map((r) => `${r.attackerName}的「${r.situation.label}」是本场值得关注的对位`)
          .join(";") + "。"
      : "两队近期同主客场比赛可比数据不足,暂无法给出关键对位。";

  return (
    <section className={pageStyles.section}>
      <h2 className={pageStyles.sectionTitle}>
        <span className={pageStyles.sectionBar} aria-hidden />
        本场攻防对位
      </h2>
      <p className={styles.windowNote}>
        {homeName}({home.label_zh}) · {awayName}({away.label_zh})——箭头左边是进攻方的场均产出,
        右边是防守方在同类型场均让出的量。「关键对位」只在进攻方产出、防守方让出同时
        高于同联赛同场景的基准均值时才标记,不是原始数字大小排的。
      </p>
      <div className={styles.card}>
        <MatchupBlock
          attackerName={homeName}
          defenderName={awayName}
          attackerMatches={home.matches}
          defenderMatches={away.matches}
          rows={homeAttack}
          highlighted={highlighted}
        />
        <MatchupBlock
          attackerName={awayName}
          defenderName={homeName}
          attackerMatches={away.matches}
          defenderMatches={home.matches}
          rows={awayAttack}
          highlighted={highlighted}
        />
        <p className={styles.summary}>{summary}</p>
        <p className={styles.footNote}>
          「禁区内射门」这一行没有 xG 拆分(数据源本身不带),用射门次数代替。
          数据不足的项不代表实测为 0;联赛基准样本不足时该类型不参与「关键对位」评选。
        </p>
      </div>
    </section>
  );
}
