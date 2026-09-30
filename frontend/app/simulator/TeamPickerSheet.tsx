"use client";

import { useState } from "react";
import type { TeamParams } from "@/features/simulator/types";
import { Sheet } from "./Sheet";
import styles from "./simulator.module.css";

/** 选球队面板:本联赛球队,对面那支置灰。取代原来的主/客队下拉。 */
export function TeamPickerSheet({
  teams,
  current,
  disabledId,
  onPick,
  onClose,
}: {
  teams: TeamParams[];
  current: number;
  disabledId: number | null;
  onPick: (teamId: number) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const list = teams.filter((t) => !q || (t.name_zh ?? "").toLowerCase().includes(q));
  return (
    <Sheet title="选择球队" onClose={onClose} testId="team-picker">
      <div className={styles.sheetHead}>
        <input className={styles.sheetSearch} type="search" placeholder="搜索球队" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="搜索球队" />
        <button type="button" className={styles.linkBtn} onClick={onClose}>
          取消
        </button>
      </div>
      {list.map((t) => {
        const disabled = t.team_id === disabledId;
        return (
          <button
            key={t.team_id}
            type="button"
            className={`${styles.pickRow} ${t.team_id === current ? styles.pickRowActive : ""}`}
            disabled={disabled}
            onClick={() => onPick(t.team_id)}
            data-testid={`team-${t.team_id}`}
          >
            <span className={styles.pickMain}>
              <span className={styles.pickName}>{t.name_zh}</span>
              <span className={styles.muted}>{t.last_lineup ? `最近首发 ${t.last_lineup.date}` : "上一场首发阵容不可用"}</span>
            </span>
            {disabled ? <span className={styles.muted}>对方球队</span> : null}
          </button>
        );
      })}
    </Sheet>
  );
}
