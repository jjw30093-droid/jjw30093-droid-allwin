/**
 * 裁判信息卡(2026-08-24,对齐 FotMob refereeCard):头像 + 姓名 + 国籍,
 * 加黄牌/犯规两个每场均值统计块(进度条 + 联赛均值刻度 + 服务端评级)。
 *
 * 数据全部来自 /matches/{id} 的 referee_* 字段(infoBox.Referee 原样投影,
 * migrations/core/0010)。评级 average_type(below/average/above)是 FotMob
 * 服务端算好的——已用 60 条实网样本证伪"可由 fill_percentage 反推",这里
 * 一律直用,不自算阈值。配色纪律:判罚尺度没有好坏之分,统一中性色 + 文字
 * 表达方向,不套"绿=好/红=坏"(CLAUDE.md §11.2:红只用于真实错误)。
 *
 * 头像与球员头像同一套机制:用 referee_id 拼 FotMob CDN 的 playerimages
 * URL(PlayerAvatar,经站长明确批准的热链例外);国旗不展示图片(来源
 * imgUrl 是 FotMob CDN 的 teamlogo 国旗,本站没有对应的同源代理路径,
 * 热链例外只覆盖人物头像)——国籍用文字如实展示。
 *
 * 降级链:有 stats → 完整卡;只有姓名(存量数据约 71% 场次如此)→ 只渲染
 * 姓名行,不画空进度条;连姓名都没有 → 整卡 null。
 */

import type { MatchDetailResponse } from "@/lib/api-v1";
import { PlayerAvatar } from "@/components/players/PlayerAvatar";
import { REFEREE_AVERAGE_TYPE_ZH, REFEREE_STAT_ZH } from "@/components/matches/zh";
import styles from "./RefereeCard.module.css";

type Match = MatchDetailResponse["match"];
type RefereeStat = NonNullable<Match["referee_stats"]>[number];

/** 只有 perMatch 两项(黄牌/犯规)上卡;matches/redCards 等 total 项与
 * type="unknown" 的占位项不展示——total 项没有联赛均值与评级,单独一个
 * 累计数字对"这场判罚会松还是紧"没有解释力。 */
function perMatchStats(stats: RefereeStat[] | undefined): RefereeStat[] {
  return (stats ?? []).filter(
    (s) => s.value_type === "perMatch" && REFEREE_STAT_ZH[s.type] != null,
  );
}

function StatBlock({ stat }: { stat: RefereeStat }) {
  const label = REFEREE_STAT_ZH[stat.type] ?? stat.type;
  const ratingZh =
    stat.average_type != null ? REFEREE_AVERAGE_TYPE_ZH[stat.average_type] ?? null : null;
  const fill = stat.fill_percentage;
  const avgPos = stat.average_percentage;
  return (
    <div className={styles.statBlock}>
      <div className={styles.statHead}>
        <span className={styles.statLabel}>{label}</span>
        <span className={styles.statValue}>
          <b className="num">{stat.value.toFixed(1)}</b>
          <span className={styles.statUnit}>/ 场</span>
        </span>
      </div>
      {fill != null && (
        <div
          className={styles.bar}
          role="img"
          aria-label={`${label}每场 ${stat.value.toFixed(1)}${
            stat.average != null ? `,联赛平均 ${stat.average.toFixed(1)}` : ""
          }`}
        >
          <div
            className={styles.barFill}
            style={{ width: `${Math.max(0, Math.min(100, fill))}%` }}
          />
          {avgPos != null && (
            <div
              className={styles.avgTick}
              style={{ left: `${Math.max(0, Math.min(100, avgPos))}%` }}
              title={
                stat.average != null ? `联赛平均 ${stat.average.toFixed(1)}` : "联赛平均"
              }
            />
          )}
        </div>
      )}
      {ratingZh && <span className={styles.rating}>{ratingZh}</span>}
    </div>
  );
}

export function RefereeCard({ match }: { match: Match }) {
  const name = match.referee;
  if (!name) return null;
  const stats = perMatchStats(match.referee_stats);
  const country = match.referee_country;

  return (
    <section className={styles.card} aria-label="裁判信息" data-testid="referee-card">
      <h3 className={styles.title}>裁判</h3>
      <div className={styles.person}>
        {match.referee_id != null && (
          <PlayerAvatar
            playerId={match.referee_id}
            playerName={name}
            size={40}
            decorative={false}
            accessibleName={`${name} 头像`}
          />
        )}
        <div className={styles.personText}>
          <span className={styles.name}>{name}</span>
          {country && <span className={styles.country}>{country}</span>}
        </div>
      </div>
      {stats.length > 0 && (
        <div className={styles.statsGrid}>
          {stats.map((s) => (
            <StatBlock key={s.type} stat={s} />
          ))}
        </div>
      )}
      {stats.length > 0 && (
        <p className={styles.note}>
          该裁判本赛季场均执法数据;刻度线为同联赛裁判平均值,评级由数据源按联赛基准给出。
        </p>
      )}
    </section>
  );
}
