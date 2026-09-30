// 排阵的纯逻辑:阵型可选项、阵型槽位、切换阵型时的就近重新分配、拖放落位。
// 槽位的位置分组只查 position_map(单一映射表);阵型模板(formations)只提供坐标。

import { fPos, type SlotAssign, type TeamSetup } from "./engine";
import type { PosGroup, SimParams } from "./types";

/** 必须支持的阵型(Phase 3);其余有模板且在 position_map 里的阵型排在后面。 */
export const CORE_FORMATIONS = ["4-3-3", "4-2-3-1", "4-4-2", "4-1-4-1", "3-5-2", "3-4-3", "3-4-2-1", "5-3-2", "5-4-1"];
const FALLBACK_FORMATION = "4-2-3-1";

function gridXY(pid: number): { x: number; y: number } {
  const col = pid % 10;
  const row = Math.floor(pid / 10);
  return { x: Math.min(0.92, Math.max(0.08, (col - 1) / 8)), y: Math.min(0.9, row / 12) };
}

/** 该阵型的 11 个槽位(模板坐标 + position_map 分组);模板或映射不完整时返回 null。 */
export function formationSlots(params: SimParams, formation: string): Omit<SlotAssign, "playerId">[] | null {
  const tpl = params.formations[formation];
  const map = params.position_map[formation];
  if (!tpl || !map || tpl.slots.length !== 11) return null;
  const slots = tpl.slots.map((s) => ({ positionId: s.position_id, group: map[String(s.position_id)] as PosGroup | undefined, x: s.x, y: s.y }));
  if (slots.some((s) => !s.group) || slots.filter((s) => s.group === "GK").length !== 1) return null;
  return slots as Omit<SlotAssign, "playerId">[];
}

export function formationChoices(params: SimParams): string[] {
  const ok = (f: string) => formationSlots(params, f) !== null;
  const core = CORE_FORMATIONS.filter(ok);
  const rest = Object.keys(params.formations).filter((f) => !CORE_FORMATIONS.includes(f) && ok(f)).sort();
  return [...core, ...rest];
}

/** 最近一场联赛首发(没有则用兜底阵型的空槽位);侧重点与休息设定保留调用方的值。 */
export function lastLineupSetup(params: SimParams, teamId: number, keep?: Pick<TeamSetup, "focuses" | "shortRest">): TeamSetup {
  const team = params.teams[String(teamId)];
  const ll = team.last_lineup;
  const formation = ll?.formation ?? FALLBACK_FORMATION;
  const tpl = params.formations[formation];
  const slotOf = (positionId: number, playerId: string | null): SlotAssign => {
    const t = tpl?.slots.find((x) => x.position_id === positionId);
    const g = gridXY(positionId);
    return {
      positionId,
      group: params.position_map[formation]?.[String(positionId)] as PosGroup,
      playerId,
      x: t?.x ?? g.x,
      y: t?.y ?? g.y,
    };
  };
  return {
    teamId,
    formation,
    focuses: keep?.focuses ?? [],
    shortRest: keep?.shortRest ?? false,
    slots: ll
      ? ll.starters.map((s) => slotOf(s.position_id, s.player_id))
      : (tpl?.slots ?? []).map((s) => slotOf(s.position_id, null)),
  };
}

/**
 * 切换阵型:已上场球员按所在位置分组就近落到新阵型的槽位上,不换人。
 * 代价(越小越好,取全局最优分配):
 *   原槽位分组 → 新槽位分组:同组 0 < 相邻 < 其他 < 门将互换(按 f_pos);
 *   其次球员主要位置与新槽位的 f_pos;最后左右位置距离(左边锋留在左边)。
 * 空位不参与代价,落在剩下的槽位上。
 */
export function reassignFormation(params: SimParams, setup: TeamSetup, formation: string): TeamSetup | null {
  const target = formationSlots(params, formation);
  if (!target) return null;
  const from = setup.slots;
  const n = target.length;
  const cost = from.map((s) => {
    const p = s.playerId ? params.players[s.playerId] : null;
    return target.map((t) =>
      p ? (1 - fPos(s.group, t.group)) * 100 + (1 - fPos(p.main_position, t.group)) * 10 + Math.abs(s.x - t.x) : 0,
    );
  });
  // 状态压缩 DP:依次给第 i 名球员选槽位(mask = 已占用槽位),n = 11 → 2048 个状态
  const size = 1 << n;
  const best = new Float64Array(size).fill(Infinity);
  const choice = new Int8Array(size).fill(-1);
  best[0] = 0;
  for (let mask = 0; mask < size; mask++) {
    if (best[mask] === Infinity) continue;
    const i = popcount(mask);
    if (i >= from.length) continue;
    for (let j = 0; j < n; j++) {
      if (mask & (1 << j)) continue;
      const next = mask | (1 << j);
      const c = best[mask] + cost[i][j];
      if (c < best[next]) {
        best[next] = c;
        choice[next] = j;
      }
    }
  }
  // 回溯:在放满 k 名球员的状态里取总代价最小者
  const k = Math.min(from.length, n);
  const assign: (string | null)[] = new Array(n).fill(null);
  let mask = -1;
  for (let m = 0; m < size; m++) {
    if (popcount(m) === k && (mask < 0 || best[m] < best[mask])) mask = m;
  }
  for (let i = k - 1; i >= 0; i--) {
    const j = choice[mask];
    assign[j] = from[i].playerId;
    mask &= ~(1 << j);
  }
  return { ...setup, formation, slots: target.map((t, j) => ({ ...t, playerId: assign[j] })) };
}

function popcount(x: number): number {
  let c = 0;
  while (x) {
    x &= x - 1;
    c++;
  }
  return c;
}

/** 两个首发位置互换。 */
export function swapSlots(setup: TeamSetup, a: number, b: number): TeamSetup {
  if (a === b) return setup;
  const slots = setup.slots.map((s) => ({ ...s }));
  [slots[a].playerId, slots[b].playerId] = [slots[b].playerId, slots[a].playerId];
  return { ...setup, slots };
}

/** 阵容池球员换上某个位置(原位置球员回到阵容池)。 */
export function placeFromPool(setup: TeamSetup, slot: number, playerId: string): TeamSetup {
  if (setup.slots.some((s) => s.playerId === playerId)) return setup;
  return { ...setup, slots: setup.slots.map((s, i) => (i === slot ? { ...s, playerId } : s)) };
}

// 拖放 id:球场位置 "slot:<下标>",阵容池球员 "pool:<player_id>"
export const slotId = (i: number) => `slot:${i}`;
export const poolId = (pid: string) => `pool:${pid}`;

/** 拖放结果 → 新的排阵;落在空白处或拖到自己身上返回 null。 */
export function applyDrop(setup: TeamSetup, activeId: string, overId: string | null): TeamSetup | null {
  if (!overId?.startsWith("slot:")) return null;
  const to = Number(overId.slice(5));
  if (activeId.startsWith("slot:")) {
    const from = Number(activeId.slice(5));
    return from === to ? null : swapSlots(setup, from, to);
  }
  if (activeId.startsWith("pool:")) return placeFromPool(setup, to, activeId.slice(5));
  return null;
}
