// 模拟器测试用的最小参数(4-4-2,主队 10 = h*,客队 20 = a*),与 simulator-wizard.test.ts 同构。
// 不是 *.test 文件,不会被 vitest 当测试收集。

import type { MatchSetup, SlotAssign, TeamSetup } from "@/features/simulator/engine";
import type { PlayerParams, PosGroup, SimParams } from "@/features/simulator/types";

export const SLOTS: [number, PosGroup][] = [
  [11, "GK"], [32, "FB"], [34, "CB"], [36, "CB"], [38, "FB"], [72, "W"], [74, "CM"], [76, "CM"], [78, "W"], [104, "ST"], [106, "ST"],
];

function player(id: string, main: PosGroup, over: Partial<PlayerParams> = {}): PlayerParams {
  return {
    player_id: id, name_zh: `球员${id}`, name_en: `Player ${id}`, shirt_number: id.replace(/\D/g, "") || "1", team_id: id.startsWith("h") ? 10 : 20,
    usual_position_id: 1, top_formation: "4-4-2", top_position_id: 32, top_position_starts: 5, starts: 5, main_position: main,
    position_source: "rule_decoded", position_unverified: true, minutes: 900, a_p: 0.1, npxg90: 0.2, xa90: 0.1, shots90: 1, headers90: 0,
    penalties_taken: 0, penalties_scored: 0, r_p: 5, g_p: main === "GK" ? 0 : null, ...over,
  } as PlayerParams;
}

export function simParams(): SimParams {
  const q = (lo: number, hi: number) => Array.from({ length: 19 }, (_, i) => lo + ((hi - lo) * i) / 18);
  const players: Record<string, PlayerParams> = {};
  for (const prefix of ["h", "a"]) {
    SLOTS.forEach(([, g], i) => (players[`${prefix}${i}`] = player(`${prefix}${i}`, g)));
  }
  const team = (id: number, prefix: string) => ({
    team_id: id, league_id: 1, name_zh: `队${id}`, A: { open: 1, counter: 0.2, setpiece: 0.4, penalty: 0.1 }, D: { open: 0.7, counter: 0.1, setpiece: 0.3, penalty: 0.1 },
    last_lineup: { match_id: 1, date: "2026-09-19", formation: "4-4-2", starters: SLOTS.map(([pid], i) => ({ player_id: `${prefix}${i}`, position_id: pid })) },
    squad: SLOTS.map((_, i) => `${prefix}${i}`),
  });
  return {
    meta: { generated_at: "2026-09-29T00:00:00Z", model_version: "v0.3", uncalibrated: true },
    leagues: {
      "1": {
        league_id: 1, finished_matches: 400,
        mu: { open: 0.8, counter: 0.15, setpiece: 0.35, penalty: 0.1, owngoal: 0.05 },
        shot_xg_quantiles: { probs: [], open: q(0.02, 0.4), counter: q(0.03, 0.5), setpiece: q(0.02, 0.35), penalty: q(0.79, 0.79) },
        shot_xg_mean: { open: 0.1, counter: 0.16, setpiece: 0.1, penalty: 0.79 },
        red_card_rate: 0.06, penalty_rate: 0.12, penalty_conversion: 0.8,
        goal_timing: { buckets: [], goals: [], factor: [1, 1, 1, 1, 1, 1] },
        stoppage_mean: { first_half: 3, second_half: 6 },
        stoppage_distribution: { first_half: { "3": 1 }, second_half: { "6": 1 } },
        gk_xgot_faced_per90: 1.35,
      },
    },
    formations: { "4-4-2": { samples: 10, slots: SLOTS.map(([pid]) => ({ position_id: pid, x: 0.5, y: 0.5 })) } },
    position_map: { "4-4-2": Object.fromEntries(SLOTS.map(([pid, g]) => [String(pid), g])) },
    teams: { "10": team(10, "h"), "20": team(20, "a") },
    players,
    fixtures: {},
  } as unknown as SimParams;
}

function slotsFor(prefix: string): SlotAssign[] {
  return SLOTS.map(([pid, g], i) => ({ positionId: pid, group: g, playerId: `${prefix}${i}`, x: 0.5, y: 0.5 }));
}

export function teamSetup(teamId: number, prefix: string): TeamSetup {
  return { teamId, formation: "4-4-2", slots: slotsFor(prefix), focuses: [], shortRest: false };
}

export function matchSetup(): MatchSetup {
  return { leagueId: 1, home: teamSetup(10, "h"), away: teamSetup(20, "a"), fixtureId: null, chaos: false };
}

/** FNV-1a 32 位:给事件流做指纹,判断"同一编号的模拟结果与改动前完全一致"。 */
export function fnv1a(s: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, "0");
}
