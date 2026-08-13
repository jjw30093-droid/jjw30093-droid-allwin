"use client";

/**
 * 市场卡(单张):结论区常驻,驱动因子折叠(赛前比赛详情页首要内容)。
 * 2026-08-14 重设计(Claude Design 定稿):结论区收紧成"倾向 + 历史命中率"
 * 两格,不再是一整段话——estimate/sample_size 降级为小字或收进折叠。
 *
 * 结论只来自 backend/queries/market_cards.py 查表——本组件不重新计算任何
 * 统计意义上的"信号",只负责把已有的判断翻译成人话并折叠归因。
 *
 * signal_grade 为 null 时(外样本不单调,或该盘口线还没跑过标定)绝不渲染
 * "倾向"文案——data_quality='ok' 但 signal_grade=null 是真实存在的状态
 * (预估值算得出来、历史命中率也查得到,但那个命中率没有通过稳定性检验,
 * 展示它会被读成"有信号"而实际没有)。四种降级态卡片保持满尺寸,不缩水
 * (站长决定:降级不等于该卡片不重要,只是不给方向性结论)。
 */

import type { ReactNode } from "react";
import { TEAM_STAT_LABELS } from "@/components/matches/zh";
import type { MarketCard as MarketCardData } from "@/lib/api-v1";
import styles from "./MarketCard.module.css";

const DRIVER_LABEL: Record<string, string> = Object.fromEntries(
  TEAM_STAT_LABELS.map((s) => [s.key, s.label]),
);

const LEAN_ZH: Record<string, string> = { over: "偏大", under: "偏小" };

function fmt(v: number | null | undefined, digits = 1): string {
  return v == null ? "—" : v.toFixed(digits);
}

type Driver = MarketCardData["driver_factors"][number];

/** 主客两份 driver_factors 按 key 对齐成一张表的行,而不是并排两张表。 */
function mergeDrivers(home: Driver[], away: Driver[]): { key: string; home: number | null; away: number | null }[] {
  const awayByKey = new Map(away.map((d) => [d.key, d]));
  return home.map((h) => ({
    key: h.key,
    home: h.for.avg ?? null,
    away: awayByKey.get(h.key)?.for.avg ?? null,
  }));
}

type DegradedReason = {
  pill: string;
  cellNote: string;
  fullNote: ReactNode;
};

function degradedReason(card: MarketCardData): DegradedReason | null {
  const { data_quality, signal_grade } = card;
  if (data_quality === "no_history") {
    return {
      pill: "样本不足",
      cellNote: "历史数据补采中",
      fullNote: "该联赛历史数据补采中,暂无法给出数据倾向。",
    };
  }
  if (data_quality === "insufficient_sample") {
    return {
      pill: "样本不足",
      cellNote: "场次不足",
      fullNote: "两队近期同联赛历史场次不足,暂无法给出数据倾向。",
    };
  }
  if (signal_grade == null) {
    return data_quality === "no_calibration"
      ? {
          pill: "未标定",
          cellNote: "暂无历史回测",
          fullNote: "该盘口线暂无历史回测数据,仅展示两队近期数据对比。",
        }
      : {
          pill: "未标定",
          cellNote: "样本外测试不稳定",
          fullNote: "有历史数据,但这个盘口线在样本外测试中不够稳定,暂不给出倾向,仅展示数据对比。",
        };
  }
  return null;
}

export function MarketCard({ card }: { card: MarketCardData }) {
  const degraded = degradedReason(card);
  const merged = mergeDrivers(card.driver_factors, card.driver_factors_away);

  return (
    <section className={styles.card}>
      <header className={styles.head}>
        <div className={styles.label}>
          <h3>{card.label}</h3>
          <span className={styles.line}>盘口线 {card.line}</span>
        </div>
        {degraded ? (
          <span className={styles.degradedPill}>{degraded.pill}</span>
        ) : (
          <span className={styles.grade}>{card.signal_grade}</span>
        )}
      </header>

      <div className={styles.verdictGrid}>
        {degraded ? (
          <div className={styles.leanCellEmpty}>
            <span className={styles.leanEmptyValue}>暂无倾向</span>
            <span className={styles.leanEmptyReason}>{degraded.cellNote}</span>
          </div>
        ) : (
          <div className={styles.leanCell}>
            <span className={`${styles.leanValue} num`}>{LEAN_ZH[card.lean ?? ""] ?? "—"}</span>
            <span className={styles.leanCaption}>数据倾向</span>
          </div>
        )}
        <div className={styles.hitCell}>
          <span
            className={`${styles.hitValue} num`}
            data-empty={degraded || card.hit_rate == null ? "true" : undefined}
          >
            {degraded || card.hit_rate == null ? "—" : `${(card.hit_rate * 100).toFixed(0)}%`}
          </span>
          <span className={styles.hitCaption}>历史命中率</span>
        </div>
      </div>

      {degraded ? (
        <p className={styles.note}>{degraded.fullNote}</p>
      ) : (
        <p className={styles.note}>
          历史上两队近况落在同一档位时,{card.line} 线
          {card.market === "goals" ? "大球" : "过线"}的比例是{" "}
          <b className={styles.noteStrong}>
            <span className="num">{card.hit_rate != null ? `${(card.hit_rate * 100).toFixed(0)}%` : "—"}</span>
          </b>
          (样本 <span className={`num ${styles.noteNum}`}>{card.sample_size ?? 0}</span> 场
          {card.calibration_scope === "all_leagues" ? ",跨联赛合并统计" : ""})。这是历史同类样本的兑现比例,
          <b className={styles.noteStrong}>不是这场比赛的胜率</b>。
        </p>
      )}

      <details className={styles.details}>
        <summary>为什么 · 驱动因子对比</summary>
        <table className={styles.driverTable}>
          <thead>
            <tr>
              <th>指标</th>
              <th className="num">主队 近10场均值</th>
              <th className="num">客队 近10场均值</th>
            </tr>
          </thead>
          <tbody>
            {merged.map((row) => (
              <tr key={row.key}>
                <td>{DRIVER_LABEL[row.key] ?? row.key}</td>
                <td className="num">{fmt(row.home)}</td>
                <td className="num">{fmt(row.away)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className={styles.note}>
          {card.estimate != null && (
            <>
              两队近 10 场自身历史均值合计 <span className="num">{card.estimate}</span>。
            </>
          )}
          倾向来自离线历史回测,不构成投注建议;样本量不足 3 场的指标显示 &ldquo;—&rdquo;。
        </p>
      </details>
    </section>
  );
}
