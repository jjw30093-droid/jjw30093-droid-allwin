"use client";

import { useState } from "react";
import {
  DndContext,
  DragOverlay,
  getClientRect,
  MouseSensor,
  pointerWithin,
  TouchSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
  type Modifier,
} from "@dnd-kit/core";
import { FootballPitchBackground } from "@/components/matches/FootballPitchBackground";
import { fPos, type SlotAssign, type TeamSetup } from "@/features/simulator/engine";
import { applyDrop, formationChoices, lastLineupSetup, placeFromPool, poolId, reassignFormation, slotId, swapSlots } from "@/features/simulator/formation";
import type { PlayerParams, PosGroup, SimParams } from "@/features/simulator/types";
import styles from "./simulator.module.css";

export const POS_LABEL: Record<PosGroup, string> = {
  GK: "门将", CB: "中卫", FB: "边后卫", DM: "后腰", CM: "中场", AM: "前腰", W: "边锋", ST: "中锋",
};

// 中文名 → 英文名;任何情况下都不显示球员 id。
export function displayName(p: PlayerParams): string {
  return p.name_zh || p.name_en || "未知球员";
}

const LINE_GROUPS: { label: string; members: PosGroup[] }[] = [
  { label: "门将", members: ["GK"] },
  { label: "后卫", members: ["CB", "FB"] },
  { label: "中场", members: ["DM", "CM", "AM"] },
  { label: "前锋", members: ["W", "ST"] },
];
const MIN_MINUTES_DEFAULT = 90;
// 球场位置用 transform 把圆点对准站位坐标;dnd-kit 默认量尺寸时去掉 transform,会让落点整体偏右下,这里改为按实际渲染位置量
const MEASURING = { droppable: { measure: getClientRect } };

/** 拖动时的浮层中心对准指针(阵容池的行很宽,不对准的话浮层会停在行的左端)。 */
const centerOnPointer: Modifier = ({ activatorEvent, draggingNodeRect, transform }) => {
  if (!draggingNodeRect || !activatorEvent) return transform;
  const e = activatorEvent as MouseEvent & TouchEvent;
  const pt = e.touches?.[0] ?? e.changedTouches?.[0] ?? e;
  if (typeof pt.clientX !== "number") return transform;
  return {
    ...transform,
    x: transform.x + pt.clientX - draggingNodeRect.left - draggingNodeRect.width / 2,
    y: transform.y + pt.clientY - draggingNodeRect.top - draggingNodeRect.height / 2,
  };
};

function PitchSlot({
  slot,
  i,
  player,
  selected,
  dragging,
  onClick,
}: {
  slot: SlotAssign;
  i: number;
  player: PlayerParams | null;
  selected: boolean;
  dragging: boolean;
  onClick: () => void;
}) {
  const { setNodeRef: setDragRef, attributes, listeners, isDragging } = useDraggable({ id: slotId(i), disabled: !player });
  const { setNodeRef: setDropRef, isOver } = useDroppable({ id: slotId(i) });
  const f = player ? fPos(player.main_position, slot.group) : 1;
  return (
    <button
      ref={(el) => {
        setDragRef(el);
        setDropRef(el);
      }}
      {...attributes}
      {...listeners}
      type="button"
      className={`${styles.slot} ${selected ? styles.slotSelected : ""} ${slot.x > 0.75 ? styles.slotRightEdge : ""} ${isOver && !isDragging ? styles.slotDropOver : ""} ${isDragging ? styles.dragSource : ""} ${dragging ? styles.slotDropTarget : ""}`}
      style={{ left: `${slot.x * 100}%`, top: `min(${(1 - slot.y) * 100}%, calc(100% - var(--slot-bottom, 52px)))` }}
      onClick={onClick}
      aria-label={`${POS_LABEL[slot.group]}位置:${player ? displayName(player) : "空"}`}
      data-testid={`slot-${i}`}
    >
      <span className={styles.dot}>{player?.shirt_number ?? "?"}</span>
      <span className={styles.slotName}>{player ? displayName(player) : "空位"}</span>
      <span className={`${styles.slotGroup} ${f < 1 ? styles.slotWarn : ""}`}>
        {f < 1 && player ? (
          <>
            <span className={styles.warnLong}>{`${player.main_position}→${slot.group} `}</span>×{f}
          </>
        ) : (
          slot.group
        )}
      </span>
    </button>
  );
}

function PoolRow({ p, onClick }: { p: PlayerParams; onClick: () => void }) {
  const { setNodeRef, attributes, listeners, isDragging } = useDraggable({ id: poolId(p.player_id) });
  return (
    <button
      ref={setNodeRef}
      {...attributes}
      {...listeners}
      type="button"
      className={`${styles.benchRow} ${isDragging ? styles.dragSource : ""}`}
      onClick={onClick}
      data-testid={`pool-${p.player_id}`}
    >
      <span className={styles.num}>{p.shirt_number ?? "-"}</span>
      <span>
        {displayName(p)} · {POS_LABEL[p.main_position]}
      </span>
      <span className={styles.muted}>{p.minutes} 分钟</span>
    </button>
  );
}

export function LineupEditor({
  params,
  sideLabel,
  setup,
  lastLineupDate,
  selected,
  onSelect,
  onChange,
  showHint = false,
}: {
  params: SimParams;
  sideLabel: string;
  setup: TeamSetup;
  lastLineupDate: string | null;
  selected: number | null;
  onSelect: (i: number | null) => void;
  onChange: (next: TeamSetup) => void;
  /** 操作说明全页只显示一次(主队编辑器);选中位置时各队仍显示自己的点选提示 */
  showHint?: boolean;
}) {
  const [showAll, setShowAll] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);
  // 鼠标移动 6px 才开始拖动(点击照常是点选);触屏长按 250ms 才开始拖动,轻点仍是点选,滑动仍是滚动页面
  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 250, tolerance: 6 } }),
  );
  const team = params.teams[String(setup.teamId)];
  const inXi = new Set(setup.slots.map((s) => s.playerId));
  const pool = team.squad.filter((id) => !inXi.has(id) && params.players[id]).map((id) => params.players[id]);
  const hiddenCount = pool.filter((p) => p.minutes < MIN_MINUTES_DEFAULT).length;
  const visible = showAll ? pool : pool.filter((p) => p.minutes >= MIN_MINUTES_DEFAULT);
  const grouped = LINE_GROUPS.map((g) => ({
    label: g.label,
    players: visible.filter((p) => g.members.includes(p.main_position)).sort((a, b) => b.minutes - a.minutes),
  }));
  const choices = formationChoices(params);
  const formations = choices.includes(setup.formation) ? choices : [setup.formation, ...choices];

  const clickSlot = (i: number) => {
    if (selected === null) return onSelect(i);
    if (selected === i) return onSelect(null);
    onChange(swapSlots(setup, selected, i));
    onSelect(null);
  };

  const clickBench = (id: string) => {
    if (selected === null) return;
    onChange(placeFromPool(setup, selected, id));
    onSelect(null);
  };

  const onDragStart = (e: DragStartEvent) => {
    setActiveId(String(e.active.id));
    onSelect(null);
  };
  const onDragEnd = (e: DragEndEvent) => {
    setActiveId(null);
    const next = applyDrop(setup, String(e.active.id), e.over ? String(e.over.id) : null);
    if (next) onChange(next);
  };

  const activePlayer = activeId
    ? params.players[
        activeId.startsWith("slot:") ? (setup.slots[Number(activeId.slice(5))]?.playerId ?? "") : activeId.slice(5)
      ]
    : null;

  return (
    <DndContext id={`lineup-${sideLabel}`} sensors={sensors} collisionDetection={pointerWithin} measuring={MEASURING} onDragStart={onDragStart} onDragEnd={onDragEnd} onDragCancel={() => setActiveId(null)}>
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
        <div className={styles.formationBar}>
          <label className={styles.field}>
            阵型
            <select
              className={styles.select}
              value={setup.formation}
              data-testid={`formation-${sideLabel}`}
              onChange={(e) => {
                const next = reassignFormation(params, setup, e.target.value);
                if (next) {
                  onChange(next);
                  onSelect(null);
                }
              }}
            >
              {formations.map((f) => (
                <option key={f} value={f} disabled={!choices.includes(f)}>
                  {f}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className={styles.secondaryBtn}
            disabled={!team.last_lineup}
            onClick={() => {
              onChange(lastLineupSetup(params, setup.teamId, { focuses: setup.focuses, shortRest: setup.shortRest }));
              onSelect(null);
            }}
          >
            恢复最近一场首发
          </button>
        </div>
        <div className={styles.pitch} data-testid={`pitch-${sideLabel}`}>
          <FootballPitchBackground orientation="portrait" variant="lineup" />
          {setup.slots.map((slot, i) => (
            <PitchSlot
              key={slot.positionId}
              slot={slot}
              i={i}
              player={slot.playerId ? params.players[slot.playerId] : null}
              selected={selected === i}
              dragging={activeId !== null}
              onClick={() => clickSlot(i)}
            />
          ))}
        </div>
        <div className={styles.bench}>
          {selected !== null ? (
            <p className={styles.muted}>{`已选中 ${POS_LABEL[setup.slots[selected].group]} 位置,点下面的替补换上,或再点一名首发互换。`}</p>
          ) : showHint ? (
            <p className={styles.muted} data-testid="lineup-hint">
              拖动球员换人或互换位置（手机长按拖动，也可点选）
            </p>
          ) : null}
          <div className={styles.benchToolbar}>
            <span className={styles.muted}>
              阵容池 {visible.length} 人{!showAll && hiddenCount ? `(已隐藏出场不足 ${MIN_MINUTES_DEFAULT} 分钟的 ${hiddenCount} 人)` : ""}
            </span>
            {hiddenCount ? (
              <button type="button" className={styles.linkBtn} onClick={() => setShowAll(!showAll)}>
                {showAll ? `隐藏不足 ${MIN_MINUTES_DEFAULT} 分钟` : "显示全部"}
              </button>
            ) : null}
          </div>
          {grouped.map((g) =>
            g.players.length ? (
              <div key={g.label} className={styles.benchGroup}>
                <div className={styles.benchGroupTitle}>{g.label}</div>
                <div className={styles.benchList}>
                  {g.players.map((p) => (
                    <PoolRow key={p.player_id} p={p} onClick={() => clickBench(p.player_id)} />
                  ))}
                </div>
              </div>
            ) : null,
          )}
        </div>
      </div>
      <DragOverlay dropAnimation={null} modifiers={[centerOnPointer]} className={styles.dragOverlayBox}>
        {activePlayer ? (
          <div className={styles.dragOverlay}>
            <span className={styles.dot}>{activePlayer.shirt_number ?? "?"}</span>
            <span>{displayName(activePlayer)}</span>
          </div>
        ) : null}
      </DragOverlay>
    </DndContext>
  );
}
