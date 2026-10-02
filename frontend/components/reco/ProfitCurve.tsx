"use client";

/**
 * 每日精选盈利走势卡(2026-10-01)。文字结论在上(按每单 500 元、本金 10000 元换算),
 * 走势图在下;没有已结算精选时整块不显示。计算与 option 在 lib/profit-curve.ts。
 */
import { EChart } from "@/components/EChart";
import { useChartColors } from "@/components/charts/useChartColors";
import {
  BANKROLL_YUAN,
  STAKE_YUAN,
  buildProfitCurveOption,
  fmtYuan,
  profitScenario,
  thousands,
  type CurvePoint,
} from "@/lib/profit-curve";
import styles from "@/app/reco/reco.module.css";

export function ProfitCurve({ points }: { points: ReadonlyArray<CurvePoint> }) {
  const colors = useChartColors();
  const sc = profitScenario(points);
  if (!sc) return null;
  const up = sc.totalYuan > 0;
  return (
    <section className={styles.profitCard} data-testid="profit-curve">
      <h2 className={styles.profitTitle}>
        每单 {thousands(STAKE_YUAN)} 元、本金 {thousands(BANKROLL_YUAN)} 元的话
      </h2>
      <p className={styles.profitLead}>
        这 <b className="num">{sc.count}</b> 单累计{" "}
        <b className={`num ${up ? styles.profitUp : styles.profitDown}`}>{fmtYuan(sc.totalYuan)}</b>
        ，本金 <span className="num">{thousands(BANKROLL_YUAN)}</span> →{" "}
        <b className="num">{thousands(sc.endBankrollYuan)}</b> 元（
        <span className="num">
          {sc.pct > 0 ? "+" : ""}
          {sc.pct}%
        </span>
        ）
      </p>
      <p className={styles.profitSub}>
        中途最多从高点回落 <span className="num">{thousands(sc.maxDrawdownYuan)}</span> 元 ·{" "}
        <span className="num">
          {sc.from} 至 {sc.to}
        </span>{" "}
        · 过去的结果不代表以后
      </p>
      <EChart
        option={buildProfitCurveOption(points, colors)}
        height={200}
        ariaSummary={`盈利走势:${sc.count} 单累计 ${fmtYuan(sc.totalYuan)},${sc.from} 至 ${sc.to}`}
        showSummary={false}
      />
    </section>
  );
}
