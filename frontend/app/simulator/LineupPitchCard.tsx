"use client";

import { useRef, useState } from "react";
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
import { PlayerAvatar } from "@/components/players/PlayerAvatar";
import { TeamBadge } from "@/components/teams/TeamBadge";
import { Chip } from "@/components/ui/Chip";
import { ChipRow } from "@/components/ui/ChipRow";
import type { SlotAssign, TeamSetup } from "@/features/simulator/engine";
import { applyDrop, applyPick, formationChoices, lastLineupSetup, reassignFormation, slotId } from "@/features/simulator/formation";
import { displayName, POS_LABEL } from "@/features/simulator/labels";
import { BADGE_LABEL, BADGE_MODES, slotBadge, type BadgeMode } from "@/features/simulator/slotBadge";
import type { PlayerParams, SimParams, TeamParams } from "@/features/simulator/types";
import { PlayerPickerSheet } from "./PlayerPickerSheet";
import { TeamPickerSheet } from "./TeamPickerSheet";
import styles from "./simulator.module.css";

// 球场位置用 transform 把头像对准站位坐标;dnd-kit 默认量尺寸时去掉 transform,会让落点整体偏右下,这里改为按实际渲染位置量
const MEASURING = { droppable: { measure: getClientRect } };
/** 拖完 150ms 内的点击忽略:把球员拖回自己位置时,不要顺手把选人面板弹出来 */
const CLICK_AFTER_DRAG_MS = 150;

/** 拖动时的浮层中心对准指针。 */
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
  badge,
  dragging,
  flagged,
  onClick,
}: {
  slot: SlotAssign;
  i: number;
  player: PlayerParams | null;
  badge: BadgeMode;
  dragging: boolean;
  flagged: boolean;
  onClick: () => void;
}) {
  const { setNodeRef: setDragRef, attributes, listeners, isDragging } = useDraggable({ id: slotId(i), disabled: !player });
  const { setNodeRef: setDropRef, isOver } = useDroppable({ id: slotId(i) });
  const b = slotBadge(player, slot.group, badge);
  return (
    <button
      ref={(el) => {
        setDragRef(el);
        setDropRef(el);
      }}
      {...attributes}
      {...listeners}
      type="button"
      className={`${styles.slot} ${slot.x > 0.75 ? styles.slotRightEdge : ""} ${isOver && !isDragging ? styles.slotDropOver : ""} ${isDragging ? styles.dragSource : ""} ${dragging ? styles.slotDropTarget : ""} ${flagged ? styles.slotFlagged : ""}`}
      // 纵向按可用高度等比缩放(不是把门将夹到底边):所有排都均匀上移,不会让被夹上去的门将撞进后卫排
      style={{ left: `${slot.x * 100}%`, top: `calc(${(1 - slot.y).toFixed(4)} * (100% - var(--slot-bottom, 58px)))` }}
      onClick={onClick}
      aria-label={`${POS_LABEL[slot.group]}位置:${player ? displayName(player) : "空,点击选择球员"}`}
      data-testid={`slot-${i}`}
    >
      <span className={styles.face}>
        {player ? (
          <PlayerAvatar playerId={player.player_id} playerName={displayName(player)} shirtNumber={player.shirt_number} size={36} eager />
        ) : (
          <span className={styles.faceEmpty} aria-hidden="true">
            +
          </span>
        )}
        {b ? (
          // 球场上只放短徽标(×0.9):长形式(AM→CM ×0.9,约 95px)在 740px 半场的 3/4 人一排里
          // 会撞上邻座的徽标(曼城 4-3-3 / 3-4-3 实测),完整文字给 title,选人面板里也有真实位置
          <span
            className={`${styles.slotBadge} ${b.warn ? styles.slotBadgeWarn : ""} ${b.quiet ? styles.slotBadgeQuiet : ""}`}
            title={b.long ?? undefined}
          >
            {b.text}
          </span>
        ) : null}
      </span>
      <span className={styles.slotName}>{player ? displayName(player) : "选择球员"}</span>
    </button>
  );
}

/** 球场卡片(参照 FotMob Lineup Builder):顶栏队名 + 最近首发;球场位置 = 头像 + 名字 + 徽标;
 *  底栏 = 阵型下拉 + 徽标切换。点位置弹选人面板;球场内拖拽互换;球场下不再列整队名单。 */
export function LineupPitchCard({
  params,
  sideLabel,
  setup,
  teams,
  disabledTeamId,
  badge,
  onBadge,
  flaggedSlots,
  onChange,
  onTeamChange,
}: {
  params: SimParams;
  sideLabel: string;
  setup: TeamSetup;
  teams: TeamParams[];
  disabledTeamId: number | null;
  badge: BadgeMode;
  onBadge: (m: BadgeMode) => void;
  flaggedSlots: number[];
  onChange: (next: TeamSetup) => void;
  onTeamChange: (teamId: number) => void;
}) {
  const [picker, setPicker] = useState<number | null>(null);
  const [teamPicker, setTeamPicker] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);
  const justDragged = useRef(false);
  // 鼠标移动 6px 才开始拖动(点击照常是点选);触屏长按 250ms 才开始拖动,轻点仍是点选,滑动仍是滚动页面
  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 250, tolerance: 6 } }),
  );
  const team = params.teams[String(setup.teamId)];
  const choices = formationChoices(params);
  const formations = choices.includes(setup.formation) ? choices : [setup.formation, ...choices];
  const flagged = new Set(flaggedSlots);
  // 同一排 ≥5 人(3-5-2 / 5-3-2 / 5-4-1…)时相邻头像只隔约 54px,徽标只显示短文本
  const rowCounts = new Map<number, number>();
  for (const sl of setup.slots) rowCounts.set(Math.round(sl.y * 20), (rowCounts.get(Math.round(sl.y * 20)) ?? 0) + 1);
  const dense = Math.max(0, ...rowCounts.values()) >= 5;

  const clickSlot = (i: number) => {
    if (justDragged.current) return;
    setPicker(i);
  };
  const onDragStart = (e: DragStartEvent) => setActiveId(String(e.active.id));
  const onDragEnd = (e: DragEndEvent) => {
    setActiveId(null);
    justDragged.current = true;
    window.setTimeout(() => {
      justDragged.current = false;
    }, CLICK_AFTER_DRAG_MS);
    const next = applyDrop(setup, String(e.active.id), e.over ? String(e.over.id) : null);
    if (next) onChange(next);
  };
  const activePlayer = activeId?.startsWith("slot:") ? params.players[setup.slots[Number(activeId.slice(5))]?.playerId ?? ""] : null;

  return (
    <DndContext id={`lineup-${sideLabel}`} sensors={sensors} collisionDetection={pointerWithin} measuring={MEASURING} onDragStart={onDragStart} onDragEnd={onDragEnd} onDragCancel={() => setActiveId(null)}>
      <section className={`${styles.card} ${styles.pitchCard}`} data-testid={`pitch-card-${sideLabel}`}>
        <div className={styles.pitchHead}>
          <button type="button" className={styles.teamPick} onClick={() => setTeamPicker(true)} data-testid={`team-pick-${sideLabel}`}>
            <span className={styles.teamPickSide}>{sideLabel}</span>
            <TeamBadge teamName={team.name_zh ?? ""} crestUrl={team.crest_url} size={28} eager />
            <span className={styles.teamPickName}>{team.name_zh}</span>
            <span className={styles.teamPickChevron} aria-hidden="true" />
          </button>
          <span className={styles.muted}>{team.last_lineup ? `最近一场首发 ${team.last_lineup.date}` : "上一场首发阵容不可用"}</span>
          <button
            type="button"
            className={styles.linkBtn}
            disabled={!team.last_lineup}
            onClick={() => onChange(lastLineupSetup(params, setup.teamId, { focuses: setup.focuses, shortRest: setup.shortRest }))}
          >
            恢复最近一场首发
          </button>
        </div>
        <div className={`${styles.pitch} ${dense ? styles.pitchDense : ""}`} data-testid={`pitch-${sideLabel}`}>
          <FootballPitchBackground orientation="portrait" variant="lineup" />
          {setup.slots.map((slot, i) => (
            <PitchSlot
              key={slot.positionId}
              slot={slot}
              i={i}
              player={slot.playerId ? params.players[slot.playerId] : null}
              badge={badge}
              dragging={activeId !== null}
              flagged={flagged.has(i)}
              onClick={() => clickSlot(i)}
            />
          ))}
        </div>
        <div className={styles.pitchBar}>
          <label className={styles.field} style={{ flex: "0 1 150px", minWidth: 120 }}>
            阵型
            <select
              className={styles.select}
              value={setup.formation}
              data-testid={`formation-${sideLabel}`}
              onChange={(e) => {
                const next = reassignFormation(params, setup, e.target.value);
                if (next) onChange(next);
              }}
            >
              {formations.map((f) => (
                <option key={f} value={f} disabled={!choices.includes(f)}>
                  {f}
                </option>
              ))}
            </select>
          </label>
          <ChipRow ariaLabel="球员徽标">
            {BADGE_MODES.map((m) => (
              <Chip key={m} active={badge === m} onClick={() => onBadge(m)} testId={`badge-${m}`}>
                {BADGE_LABEL[m]}
              </Chip>
            ))}
          </ChipRow>
        </div>
        <p className={styles.muted} data-testid="lineup-hint">
          点位置换人,拖动两名首发可互换(手机长按拖动)。
        </p>
      </section>
      <DragOverlay dropAnimation={null} modifiers={[centerOnPointer]} className={styles.dragOverlayBox}>
        {activePlayer ? (
          <div className={styles.dragOverlay}>
            <PlayerAvatar playerId={activePlayer.player_id} playerName={displayName(activePlayer)} shirtNumber={activePlayer.shirt_number} size={32} />
            <span>{displayName(activePlayer)}</span>
          </div>
        ) : null}
      </DragOverlay>
      {picker != null ? (
        <PlayerPickerSheet
          params={params}
          setup={setup}
          slot={picker}
          onPick={(pid) => {
            onChange(applyPick(setup, picker, pid));
            setPicker(null);
          }}
          onClose={() => setPicker(null)}
        />
      ) : null}
      {teamPicker ? (
        <TeamPickerSheet
          teams={teams}
          current={setup.teamId}
          disabledId={disabledTeamId}
          onPick={(id) => {
            setTeamPicker(false);
            onTeamChange(id);
          }}
          onClose={() => setTeamPicker(false)}
        />
      ) : null}
    </DndContext>
  );
}
