"use client";

import { useState } from "react";
import { PlayerAvatar } from "@/components/players/PlayerAvatar";
import type { TeamSetup } from "@/features/simulator/engine";
import { displayName, POS_LABEL } from "@/features/simulator/labels";
import { MIN_MINUTES_DEFAULT, pickerRows, type PickerRow } from "@/features/simulator/picker";
import type { SimParams } from "@/features/simulator/types";
import { Sheet } from "./Sheet";
import styles from "./simulator.module.css";

/** 点球场位置后的选人面板(参照 FotMob:搜索 + 按该位置推荐 + 头像行)。只列本队阵容;没有"移除球员"(模拟必须 11 人)。 */
export function PlayerPickerSheet({
  params,
  setup,
  slot,
  onPick,
  onClose,
}: {
  params: SimParams;
  setup: TeamSetup;
  slot: number;
  onPick: (playerId: string) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [showAll, setShowAll] = useState(false);
  const group = setup.slots[slot].group;
  const rows = pickerRows(params, setup, slot, { query, showAll });
  const Row = ({ r }: { r: PickerRow }) => (
    <button type="button" className={styles.pickRow} onClick={() => onPick(r.player.player_id)} data-testid={`pick-${r.player.player_id}`}>
      <PlayerAvatar playerId={r.player.player_id} playerName={displayName(r.player)} shirtNumber={r.player.shirt_number} size={40} />
      <span className={styles.pickMain}>
        <span className={styles.pickName}>{displayName(r.player)}</span>
        <span className={styles.muted}>
          {POS_LABEL[r.player.main_position]}
          {r.inXiSlot != null ? ` · 首发${POS_LABEL[setup.slots[r.inXiSlot].group]}` : ""}
          {r.fit < 1 ? <span className={styles.pickWarn}> ×{r.fit}</span> : null}
        </span>
      </span>
      <span className={styles.muted}>{r.player.minutes} 分钟</span>
    </button>
  );
  const empty = !rows.suggested.length && !rows.others.length;
  return (
    <Sheet title={`选择球员 · ${POS_LABEL[group]}位置`} onClose={onClose} testId="player-picker">
      <div className={styles.sheetHead}>
        <input
          className={styles.sheetSearch}
          type="search"
          placeholder="搜索球员"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="搜索球员"
        />
        <button type="button" className={styles.linkBtn} onClick={onClose}>
          取消
        </button>
      </div>
      <p className={styles.sheetTitle}>
        选择球员 · {POS_LABEL[group]}位置
        {rows.current ? <span className={styles.muted}> · 当前 {displayName(rows.current)}</span> : null}
      </p>
      {rows.suggested.length ? (
        <>
          <p className={styles.sheetSection}>推荐球员</p>
          {rows.suggested.map((r) => (
            <Row key={r.player.player_id} r={r} />
          ))}
        </>
      ) : null}
      {rows.others.length ? (
        <>
          <p className={styles.sheetSection}>其他球员</p>
          {rows.others.map((r) => (
            <Row key={r.player.player_id} r={r} />
          ))}
        </>
      ) : null}
      {empty ? <p className={styles.muted}>没有匹配的球员</p> : null}
      {rows.hiddenLowMinutes > 0 ? (
        <button type="button" className={styles.linkBtn} onClick={() => setShowAll(true)}>
          显示全部(还有 {rows.hiddenLowMinutes} 人出场不足 {MIN_MINUTES_DEFAULT} 分钟)
        </button>
      ) : null}
    </Sheet>
  );
}
