"use client";

import { Chip } from "@/components/ui/Chip";
import { FOCUS_LABEL, FOCUS_LIST, focusError, type Focus, type MatchSetup, type TeamSetup } from "@/features/simulator/engine";
import { channelDeltas, FIT_THRESHOLD_PP, formatDeltas, type SideImpact } from "@/features/simulator/focusImpact";
import type { SimParams } from "@/features/simulator/types";
import styles from "./simulator.module.css";

const pct1 = (x: number) => `${(x * 100).toFixed(1)}%`;
const signedPp = (pp: number) => `${pp > 0 ? "+" : pp < 0 ? "−" : "±"}${Math.abs(pp).toFixed(1)} 个百分点`;

/** 单队侧重点卡片(从原来的两队并排块原样抽出):侧重点 Chip + 适合本场对手、渠道变化、胜率影响、休息不足。 */
export function TeamFocusCard({
  params,
  side,
  setup,
  opponentName,
  impactSetup,
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
  const ch = err ? null : channelDeltas(params, impactSetup, side);
  const toggle = (f: Focus) => {
    const focuses = setup.focuses.includes(f) ? setup.focuses.filter((x) => x !== f) : [...setup.focuses, f];
    onChange({ ...setup, focuses });
  };
  return (
    <section className={styles.card} data-testid={`focus-side-${side}`}>
      <h2 className={styles.cardTitle}>侧重点(最多 2 个)</h2>
      <div className={styles.chips}>
        {FOCUS_LIST.map((f) => {
          const gain = impact?.singlePp[f];
          const fit = gain !== undefined && gain >= FIT_THRESHOLD_PP;
          return (
            <Chip key={f} active={setup.focuses.includes(f)} onClick={() => toggle(f)}>
              {FOCUS_LABEL[f]}
              {fit ? (
                <span className={styles.fitTag} data-testid="focus-fit-tag">
                  适合本场对手
                </span>
              ) : null}
            </Chip>
          );
        })}
      </div>
      {err ? <p className={styles.hint}>{err}</p> : null}
      {ch ? (
        <div className={styles.focusEffect} data-testid="focus-channels">
          <div>本队预期进球:{ch.own.length ? formatDeltas(ch.own) : "各渠道不变"}</div>
          {ch.opp.length ? <div>对手预期进球:{formatDeltas(ch.opp)}</div> : null}
        </div>
      ) : null}
      <div className={styles.focusEffect} data-testid="focus-winrate">
        {!preparedOk ? null : !impact ? (
          <span className={styles.muted}>胜率影响计算中…</span>
        ) : impact.selected != null ? (
          <>
            本队胜率 {pct1(impact.base)} → <strong>{pct1(impact.selected)}</strong>({signedPp((impact.selected - impact.base) * 100)})
          </>
        ) : (
          <>未选侧重点:本队胜率 {pct1(impact.base)}</>
        )}
        {preparedOk && impact && side === 0 ? <span className={styles.muted}>(对阵 {opponentName},下一步可更换)</span> : null}
      </div>
      <div className={styles.chips}>
        <Chip active={setup.shortRest} onClick={() => onChange({ ...setup, shortRest: !setup.shortRest })}>
          休息不足 3 天(假设设定)
        </Chip>
      </div>
    </section>
  );
}
