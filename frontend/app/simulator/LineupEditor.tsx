"use client";

import { FootballPitchBackground } from "@/components/matches/FootballPitchBackground";
import { fPos, type TeamSetup } from "@/features/simulator/engine";
import type { PlayerParams, PosGroup, SimParams } from "@/features/simulator/types";
import styles from "./simulator.module.css";

const POS_ORDER: PosGroup[] = ["GK", "CB", "FB", "DM", "CM", "AM", "W", "ST"];
export const POS_LABEL: Record<PosGroup, string> = {
  GK: "门将", CB: "中卫", FB: "边后卫", DM: "后腰", CM: "中场", AM: "前腰", W: "边锋", ST: "中锋",
};

// 名称表里没有的球员(dim_player 缺行)退回英文名,再退回"号码 + id"。
export function displayName(p: PlayerParams): string {
  return p.name_zh || p.name_en || `#${p.shirt_number ?? "?"} (${p.player_id})`;
}

export function LineupEditor({
  params,
  sideLabel,
  setup,
  lastLineupDate,
  selected,
  onSelect,
  onChange,
}: {
  params: SimParams;
  sideLabel: string;
  setup: TeamSetup;
  lastLineupDate: string | null;
  selected: number | null;
  onSelect: (i: number | null) => void;
  onChange: (next: TeamSetup) => void;
}) {
  const team = params.teams[String(setup.teamId)];
  const inXi = new Set(setup.slots.map((s) => s.playerId));
  const bench = team.squad
    .filter((id) => !inXi.has(id) && params.players[id])
    .map((id) => params.players[id])
    .sort((a, b) => POS_ORDER.indexOf(a.main_position) - POS_ORDER.indexOf(b.main_position) || b.minutes - a.minutes);

  const clickSlot = (i: number) => {
    if (selected === null) return onSelect(i);
    if (selected === i) return onSelect(null);
    const slots = setup.slots.map((s) => ({ ...s }));
    [slots[selected].playerId, slots[i].playerId] = [slots[i].playerId, slots[selected].playerId];
    onChange({ ...setup, slots });
    onSelect(null);
  };

  const clickBench = (id: string) => {
    if (selected === null) return;
    const slots = setup.slots.map((s, i) => (i === selected ? { ...s, playerId: id } : s));
    onChange({ ...setup, slots });
    onSelect(null);
  };

  return (
    <div>
      <div className={styles.teamHead}>
        <span className={styles.teamName}>
          {sideLabel} · {team.name_zh}
        </span>
        <span className={styles.muted}>
          {setup.formation}
          {lastLineupDate ? ` · 最近一场首发 ${lastLineupDate}` : ""}
        </span>
      </div>
      <div className={styles.pitch} data-testid={`pitch-${sideLabel}`}>
        <FootballPitchBackground orientation="portrait" variant="lineup" />
        {setup.slots.map((slot, i) => {
          const p = slot.playerId ? params.players[slot.playerId] : null;
          const f = p ? fPos(p.main_position, slot.group) : 1;
          return (
            <button
              key={slot.positionId}
              type="button"
              className={`${styles.slot} ${selected === i ? styles.slotSelected : ""}`}
              style={{ left: `${slot.x * 100}%`, top: `min(${(1 - slot.y) * 100}%, calc(100% - var(--slot-bottom, 52px)))` }}
              onClick={() => clickSlot(i)}
              aria-label={`${POS_LABEL[slot.group]}位置:${p ? displayName(p) : "空"}`}
            >
              <span className={styles.dot}>{p?.shirt_number ?? "?"}</span>
              <span className={styles.slotName}>{p ? displayName(p) : "空位"}</span>
              <span className={`${styles.slotGroup} ${f < 1 ? styles.slotWarn : ""}`}>
                {f < 1 && p ? (
                  <>
                    <span className={styles.warnLong}>{`${p.main_position}→${slot.group} `}</span>×{f}
                  </>
                ) : (
                  slot.group
                )}
              </span>
            </button>
          );
        })}
      </div>
      <div className={styles.bench}>
        <p className={styles.muted}>
          {selected === null
            ? "点球场上的球员选中位置,再点替补换上;连点两名首发互换位置。球员位置为规则解码,位置未校验。"
            : `已选中 ${POS_LABEL[setup.slots[selected].group]} 位置,点下面的替补换上,或再点一名首发互换。`}
        </p>
        <div className={styles.benchList}>
          {bench.map((p) => (
            <button
              key={p.player_id}
              type="button"
              className={styles.benchRow}
              disabled={selected === null}
              onClick={() => clickBench(p.player_id)}
            >
              <span className={styles.num}>{p.shirt_number ?? "-"}</span>
              <span>
                {displayName(p)} · {POS_LABEL[p.main_position]} <span className={styles.unverified}>(位置未校验)</span>
              </span>
              <span className={styles.muted}>{p.minutes} 分钟</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
