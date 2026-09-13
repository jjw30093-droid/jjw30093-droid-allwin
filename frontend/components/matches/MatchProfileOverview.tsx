/**
 * 「数据 → 风格」子 tab 首屏总览层(2026-09 重构)。
 *
 * 回答站长提的三个问题:谁更强 / 强在哪 / 最大的几条差距——攻/守/控三组
 * 各给一根组级百分位轴(组内 ≥2 个指标都有百分位才汇总,见
 * `backend/metrics/percentile.py::group_percentile`,不用单指标冒充整组),
 * 再列出 |Δ百分位| 最大的最多 3 条差距(后端 `top_gaps` 已按 15 分位门槛
 * 过滤掉噪声级差距,不需要前端重新判断)。
 *
 * 两队分别来自**联赛主场分布**和**联赛客场分布**两套独立百分位,不在
 * 同一把绝对数值尺上——文案里必须讲清楚这一点,不能暗示"直接比大小"。
 */

"use client";

import type { ReactNode } from "react";
import pageStyles from "@/app/matches/[matchId]/match-detail.module.css";
import { CrestDot } from "./CrestDot";
import styles from "./MatchProfileOverview.module.css";
import {
  highlightSentence,
  peerSentence,
  profileOverviewFootNote,
  type DataProfile,
} from "./matchProfile";

const GROUP_LABEL: Record<string, string> = { attack: "攻", defence: "守", control: "控" };

export function MatchProfileOverview({
  homeName,
  awayName,
  homeCrestUrl,
  awayCrestUrl,
  profile,
  scopeSwitcher,
}: {
  homeName: string;
  awayName: string;
  homeCrestUrl?: string | null;
  awayCrestUrl?: string | null;
  profile: DataProfile;
  /** 「全部/相同主客场」×「近 3/5/10 场」两个切换器。由
   * `MatchProfilePanel` 注入——状态必须只有一个持有者,这里只负责摆放。 */
  scopeSwitcher?: ReactNode;
}) {
  if (!profile.home_available && !profile.away_available) {
    return (
      <section className={pageStyles.section}>
        <h2 className={pageStyles.sectionTitle}>
          <span className={pageStyles.sectionBar} aria-hidden />
          本场数据画像
        </h2>
        {/* 空态下切换器**必须照样渲染**:某个口径组合恰好取不到数据时,
            如果切换器被空态一起吞掉,用户就被锁死在一张白页上出不来了。 */}
        {scopeSwitcher}
        <p className={pageStyles.emptyText}>
          {/* 兜底文案保持 venue 中性:后端在这条分支上恒会给 unavailable_reason
              (已按口径分支),前端不该复述一个可能不成立的原因。 */}
          {profile.unavailable_reason ?? "暂无法给出联赛百分位画像。"}
        </p>
      </section>
    );
  }

  // 跨联赛赛事(欧战):没有共同的参照人群,组级百分位轴、对标球队、
  // 「最大的差距」榜全部建立在分布之上,这里一个都算不出来——不是数据缺失,
  // 是这些概念对跨联赛比赛本身不成立。总览退化为一句口径说明,具体数值
  // 由下面攻/守/控三段并排展示。
  const crossLeague = profile.comparison_mode === "cross_league_raw";

  return (
    <section className={pageStyles.section}>
      <h2 className={pageStyles.sectionTitle}>
        <span className={pageStyles.sectionBar} aria-hidden />
        本场数据画像
      </h2>
      {scopeSwitcher}
      <div className={styles.card}>
        <div className={styles.teams}>
          <span className={styles.teamName}>{homeName}</span>
          <span className={styles.vs}>{crossLeague ? "近期数据对比" : "联赛百分位对比"}</span>
          <span className={styles.teamName}>{awayName}</span>
        </div>

        {!crossLeague &&
          profile.groups.map((g) => {
            const homePeerLine = peerSentence(homeName, g.home_peers);
            const awayPeerLine = peerSentence(awayName, g.away_peers);
            return (
              <div className={styles.groupBlock} key={g.key}>
                <div className={styles.groupRow}>
                  <span className={styles.groupLabel}>{GROUP_LABEL[g.key] ?? g.key}</span>
                  {g.home_group_percentile != null && g.away_group_percentile != null ? (
                    <div
                      className={styles.groupAxis}
                      role="img"
                      aria-label={`${g.title_zh}:${homeName} 联赛第 ${Math.round(g.home_group_percentile)} 百分位,${awayName} 联赛第 ${Math.round(g.away_group_percentile)} 百分位。`}
                    >
                      <CrestDot pct={g.home_group_percentile} crestUrl={homeCrestUrl} teamName={homeName} side="home" />
                      <CrestDot pct={g.away_group_percentile} crestUrl={awayCrestUrl} teamName={awayName} side="away" />
                    </div>
                  ) : (
                    <span className={styles.groupUnavailable}>样本口径不同或数据不足,暂不作整体比较。</span>
                  )}
                </div>
                {(homePeerLine || awayPeerLine) && (
                  <p className={styles.peerLine}>
                    {[homePeerLine, awayPeerLine].filter(Boolean).join(" · ")}
                  </p>
                )}
              </div>
            );
          })}

        {!crossLeague && (
          <div className={styles.highlights}>
            <h3 className={styles.highlightsTitle}>最大的差距</h3>
            {profile.highlights.length === 0 ? (
              <p className={styles.noHighlight}>两队在本联赛的位置接近,没有拉开明显差距的项。</p>
            ) : (
              profile.highlights.map((h) => {
                const semantic =
                  profile.groups.flatMap((g) => g.metrics).find((m) => m.key === h.key)?.semantic ?? "performance";
                return (
                  <p className={styles.highlightItem} key={h.key}>
                    {highlightSentence(h, semantic, homeName, awayName, profile.venue_mode ?? "same_venue")}
                  </p>
                );
              })
            )}
          </div>
        )}

        {/* 这句话原来写死在 JSX 里,而"两套独立分布"在「全部」口径下恰好
            说反了(那时两队共用同一套分布)——措辞必须跟着实际取数口径走,
            所以收进 matchProfile.ts 按维度分支。 */}
        <p className={styles.footNote}>{profileOverviewFootNote(homeName, awayName, profile)}</p>
      </div>
    </section>
  );
}
