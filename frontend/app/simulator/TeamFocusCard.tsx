"use client";

import { exclusiveWith, FOCUS_LABEL, FOCUS_LIST, focusError, type Focus, type MatchSetup, type TeamSetup } from "@/features/simulator/engine";
import { FIT_THRESHOLD_PP, type SideImpact } from "@/features/simulator/focusImpact";
import { FOCUS_DESC } from "@/features/simulator/labels";
import type { SimParams } from "@/features/simulator/types";
import styles from "./simulator.module.css";

const pct1 = (x: number) => `${(x * 100).toFixed(1)}%`;
/** 胜率变化(百分点),带正负号;不写单位,卡片顶部的"本队胜率"已说明是胜率 */
const signed = (pp: number) => `${pp > 0.05 ? "+" : pp < -0.05 ? "−" : "±"}${Math.abs(pp).toFixed(1)}`;
const MAX_FOCUSES = 2;
/** 置灰原因里用的短名(手机上方块只有约 150px 宽,"与高位逼抢互斥"会折成两行) */
const FOCUS_SHORT: Record<Focus, string> = { setpiece: "定位球", counter: "反击", possession: "控球", press: "逼抢", crossing: "传中", lowblock: "稳守" };

/**
 * 单队战术侧重卡片:最上面是本队胜率(选了侧重点显示变化),下面 2 列方块,每块 = 名字 + 一句话说明 + 单选它时的胜率变化;
 * 与已选互斥、或已选满 2 个时置灰并写明原因;"休息不足 3 天"不是战术,单独一行开关放在最下面。
 * 胜率变化来自排阵阶段后台 worker 已算好的 SideImpact(同一模拟编号各 1000 次),这里只展示。
 */
export function TeamFocusCard({
  side,
  setup,
  opponentName,
  impact,
  preparedOk,
  onChange,
}: {
  params: SimParams;
  side: 0 | 1;
  setup: TeamSetup;
  opponentName: string;
  impactSetup: MatchSetup;
  impact: SideImpact | null;
  preparedOk: boolean;
  onChange: (next: TeamSetup) => void;
}) {
  const err = focusError(setup.focuses);
  const toggle = (f: Focus) => {
    const focuses = setup.focuses.includes(f) ? setup.focuses.filter((x) => x !== f) : [...setup.focuses, f];
    onChange({ ...setup, focuses });
  };
  const blockedReason = (f: Focus): string | null => {
    if (setup.focuses.includes(f)) return null;
    const other = exclusiveWith(f);
    if (other && setup.focuses.includes(other)) return `与${FOCUS_SHORT[other]}互斥`;
    if (setup.focuses.length >= MAX_FOCUSES) return `最多 ${MAX_FOCUSES} 个`;
    return null;
  };

  return (
    <section className={styles.card} data-testid={`focus-side-${side}`}>
      <div className={styles.focusHead}>
        <h2 className={styles.cardTitle} style={{ margin: 0 }}>
          战术侧重 <span className={styles.focusHeadNote}>最多 {MAX_FOCUSES} 个</span>
        </h2>
        <span className={styles.focusHeadNote}>
          对阵{opponentName}
          {side === 0 ? "(暂定)" : ""}
        </span>
      </div>

      <div className={styles.focusWin} data-testid="focus-winrate">
        {!preparedOk ? null : !impact ? (
          <span className={styles.muted}>本队胜率计算中…</span>
        ) : (
          <>
            <span className={styles.focusWinLabel}>本队胜率</span>
            <span className={styles.focusWinNum}>{pct1(impact.base)}</span>
            {impact.selected != null ? (
              <>
                <span className={styles.focusWinArrow} aria-hidden="true">
                  →
                </span>
                <span className={`${styles.focusWinNum} ${styles.focusWinNew}`}>{pct1(impact.selected)}</span>
                <span className={(impact.selected - impact.base) * 100 > 0.05 ? styles.focusUp : styles.focusFlat}>
                  {signed((impact.selected - impact.base) * 100)}
                </span>
              </>
            ) : null}
          </>
        )}
      </div>

      <div className={styles.focusGrid}>
        {FOCUS_LIST.map((f) => {
          const on = setup.focuses.includes(f);
          const blocked = blockedReason(f);
          const gain = impact?.singlePp[f];
          const fit = gain !== undefined && gain >= FIT_THRESHOLD_PP;
          return (
            <button
              key={f}
              type="button"
              className={`${styles.focusTile} ${on ? styles.focusTileOn : ""} ${blocked ? styles.focusTileBlocked : ""}`}
              aria-pressed={on}
              aria-disabled={blocked ? true : undefined}
              onClick={() => {
                if (!blocked) toggle(f);
              }}
              data-testid={`focus-${f}`}
            >
              <span className={styles.focusTileTop}>
                <span className={styles.focusTileName}>{FOCUS_LABEL[f]}</span>
                {blocked ? (
                  <span className={styles.focusFlat}>{blocked}</span>
                ) : !preparedOk ? null : gain === undefined ? (
                  <span className={styles.focusFlat}>…</span>
                ) : (
                  <span className={gain > 0.05 ? styles.focusUp : styles.focusFlat} data-testid={fit ? "focus-fit-tag" : undefined}>
                    {signed(gain)}
                    {fit ? " · 适合本场" : ""}
                  </span>
                )}
              </span>
              <span className={styles.focusTileDesc}>{FOCUS_DESC[f]}</span>
            </button>
          );
        })}
      </div>
      {err ? <p className={styles.hint}>{err}</p> : null}

      <div className={styles.focusRest}>
        <span>
          赛程密集 <span className={styles.focusHeadNote}>休息不足 3 天</span>
        </span>
        <button
          type="button"
          role="switch"
          aria-checked={setup.shortRest}
          aria-label="休息不足 3 天"
          className={`${styles.switch} ${setup.shortRest ? styles.switchOn : ""}`}
          onClick={() => onChange({ ...setup, shortRest: !setup.shortRest })}
          data-testid={`short-rest-${side}`}
        >
          <span className={styles.switchKnob} />
        </button>
      </div>
    </section>
  );
}
