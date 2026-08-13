"use client";

/**
 * 市场卡(单张):结论区常驻,驱动因子折叠(赛前比赛详情页首要内容)。
 *
 * 结论只来自 backend/queries/market_cards.py 查表——本组件不重新计算任何
 * 统计意义上的"信号",只负责把已有的判断翻译成人话并折叠归因。
 *
 * signal_grade 为 null 时(外样本不单调,或该盘口线还没跑过标定)绝不渲染
 * "倾向"文案——data_quality='ok' 但 signal_grade=null 是真实存在的状态
 * (预估值算得出来、历史命中率也查得到,但那个命中率没有通过稳定性检验,
 * 展示它会被读成"有信号"而实际没有)。
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

function DriverRow({
  driver,
  side,
}: {
  driver: MarketCardData["driver_factors"][number];
  side: "for" | "against";
}) {
  const label = DRIVER_LABEL[driver.key] ?? driver.key;
  const v = driver[side];
  return (
    <tr>
      <td>{label}</td>
      <td className="num">{fmt(v.avg)}</td>
      <td className="num">{v.n}</td>
    </tr>
  );
}

export function MarketCard({ card }: { card: MarketCardData }) {
  const { data_quality, signal_grade } = card;

  let verdict: ReactNode;
  if (data_quality === "no_history") {
    verdict = <p className={styles.degraded}>该联赛历史数据补采中,暂无法给出数据倾向。</p>;
  } else if (data_quality === "insufficient_sample") {
    verdict = <p className={styles.degraded}>两队近期同联赛历史场次不足,暂无法给出数据倾向。</p>;
  } else if (signal_grade == null) {
    // data_quality 可能是 'ok'(查到命中率但不单调)或 'no_calibration'
    // (这条盘口线还没跑过标定)——文案分开,但都不给方向性结论。
    verdict =
      data_quality === "no_calibration" ? (
        <p className={styles.degraded}>该盘口线暂无历史回测数据,仅展示两队近期数据对比。</p>
      ) : (
        <p className={styles.degraded}>
          有历史数据,但这个盘口线在样本外测试中不够稳定,暂不给出倾向,仅展示数据对比。
        </p>
      );
  } else {
    verdict = (
      <p className={styles.verdict}>
        数据倾向:<b className={styles.lean}>{LEAN_ZH[card.lean ?? ""] ?? "—"}</b>{" "}
        <span className={styles.grade}>{signal_grade}</span>
        <br />
        历史上两队近况落在同一档位时,{card.line} 线的{card.market === "goals" ? "大球" : "过线"}
        命中率为 <b className="num">{card.hit_rate != null ? `${(card.hit_rate * 100).toFixed(0)}%` : "—"}</b>
        （样本 <span className="num">{card.sample_size ?? 0}</span> 场
        {card.calibration_scope === "all_leagues" ? "，跨联赛合并统计" : ""}）。
      </p>
    );
  }

  return (
    <section className={styles.card}>
      <header className={styles.head}>
        <h3 className={styles.label}>{card.label}</h3>
        <span className={styles.line}>盘口线 {card.line}</span>
      </header>
      {verdict}
      {card.estimate != null && (
        <p className={styles.estimate}>
          两队近 10 场自身历史均值合计:<span className="num">{card.estimate}</span>
        </p>
      )}
      <details className={styles.details}>
        <summary>为什么 · 驱动因子</summary>
        <div className={styles.driverGrid}>
          <div>
            <h4>主队近况(自身创造 / 对手创造)</h4>
            <table className={styles.driverTable}>
              <thead>
                <tr>
                  <th>指标</th>
                  <th>创造</th>
                  <th>样本</th>
                </tr>
              </thead>
              <tbody>
                {card.driver_factors.map((d) => (
                  <DriverRow key={d.key} driver={d} side="for" />
                ))}
              </tbody>
            </table>
          </div>
          <div>
            <h4>客队近况(自身创造 / 对手创造)</h4>
            <table className={styles.driverTable}>
              <thead>
                <tr>
                  <th>指标</th>
                  <th>创造</th>
                  <th>样本</th>
                </tr>
              </thead>
              <tbody>
                {card.driver_factors_away.map((d) => (
                  <DriverRow key={d.key} driver={d} side="for" />
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <p className={styles.note}>
          数据倾向来自离线历史回测(时间序外样本验证),不构成任何投注建议;
          样本量不足 3 场的指标显示 &ldquo;—&rdquo;。
        </p>
      </details>
    </section>
  );
}
