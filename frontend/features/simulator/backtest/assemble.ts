// 快照 + 校准值 → 引擎输入(SimParams / MatchSetup)。只做拼装,模型计算全部在 engine.ts。

import { ALL_EFFECTS, V02_STATE, type Effects, type MatchSetup } from "../engine";
import type { FixtureParams, SimParams, StateMultipliers } from "../types";
import type { Prematch, Snapshot } from "./types";

export interface Cal {
  k: number;
  /** 每联赛主场系数 h;null = 用 v0.2 的 1.08 / 0.93 */
  h: Record<string, number> | null;
  w: number;
  kappa: number;
  red_own: number;
  red_opp: number;
  state: StateMultipliers;
}

export const V02_CAL: Cal = { k: 5, h: null, w: 0.7, kappa: 12, red_own: 0.75, red_opp: 1.25, state: V02_STATE };

export function simParamsFor(snap: Snapshot, cal: Cal, opts: { useMarket: boolean; hOverride?: number }): SimParams {
  const leagues: SimParams["leagues"] = {};
  for (const [lid, l] of Object.entries(snap.params.leagues)) {
    leagues[lid] = { ...l, home_advantage: opts.hOverride ?? cal.h?.[lid] };
  }
  const teams: SimParams["teams"] = {};
  for (const [tid, t] of Object.entries(snap.params.teams)) {
    teams[tid] = { ...t, A: t.A_by_k[String(cal.k)], D: t.D_by_k[String(cal.k)] };
  }
  const fixtures: Record<string, FixtureParams> = {};
  if (opts.useMarket) {
    for (const pm of snap.prematch) {
      const c = pm.crown;
      if (!c?.market_lambda) continue;
      fixtures[String(pm.match_id)] = {
        match_id: pm.match_id,
        league_id: pm.league_id,
        kickoff_at_utc: pm.kickoff_at_utc ?? "",
        home_team_id: pm.home_team_id,
        away_team_id: pm.away_team_id,
        status: "已完赛（赛前盘口）",
        final_score: null,
        ah: c.ah,
        ou: c.ou,
        market_lambda: c.market_lambda,
      };
    }
  }
  return {
    meta: { generated_at: snap.date, model_version: "backtest", uncalibrated: true },
    leagues,
    formations: snap.params.formations,
    position_map: snap.params.position_map,
    teams,
    players: snap.params.players,
    fixtures,
    calibration: { market_w: cal.w, kappa: cal.kappa, red_own: cal.red_own, red_opp: cal.red_opp, state: cal.state },
  };
}

/** 当场实际首发 U;缺首发(或位置无法解码)返回 null。 */
export function setupFor(pm: Prematch, opts: { useMarket: boolean; effects?: Effects }): MatchSetup | null {
  const { home, away } = pm.lineups;
  if (!home || !away) return null;
  const side = (tid: number, l: NonNullable<Prematch["lineups"]["home"]>) => ({
    teamId: tid,
    formation: l.formation,
    slots: l.starters.map((s) => ({ positionId: s.position_id, group: s.group, playerId: s.player_id, x: 0, y: 0 })),
    focuses: [],
    shortRest: false,
  });
  return {
    leagueId: pm.league_id,
    home: side(pm.home_team_id, home),
    away: side(pm.away_team_id, away),
    fixtureId: opts.useMarket && pm.crown?.market_lambda ? pm.match_id : null,
    chaos: false,
    effects: opts.effects ?? ALL_EFFECTS,
  };
}
