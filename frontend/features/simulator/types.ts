// 本地参数 JSON(scripts/simulator/export_params.py 导出)的形状。
// 这不是 API 响应:数据只在本地开发时从 SIMULATOR_PARAMS_PATH 读取,不经 FastAPI,
// 所以不走 OpenAPI 生成类型。

export type ShotChannel = "open" | "counter" | "setpiece" | "penalty";
export type Channel = ShotChannel | "owngoal";
export type PosGroup = "GK" | "CB" | "FB" | "DM" | "CM" | "AM" | "W" | "ST";

export const SHOT_CHANNELS: readonly ShotChannel[] = ["open", "counter", "setpiece", "penalty"];

export interface LeagueParams {
  league_id: number;
  finished_matches: number;
  mu: Record<Channel, number>;
  shot_xg_quantiles: { probs: number[] } & Record<ShotChannel, number[]>;
  shot_xg_mean: Record<ShotChannel, number>;
  red_card_rate: number;
  penalty_rate: number;
  penalty_conversion: number;
  goal_timing: { buckets: string[]; goals: number[]; factor: number[] };
  stoppage_mean: { first_half: number; second_half: number };
  gk_xgot_faced_per90: number;
}

export interface FormationSlot {
  position_id: number;
  group: PosGroup;
  x: number | null;
  y: number | null;
}

export interface LineupStarter {
  player_id: string;
  position_id: number;
  slot_group: PosGroup | null;
}

export interface TeamParams {
  team_id: number;
  league_id: number;
  name_zh: string | null;
  name_en: string | null;
  A: Record<ShotChannel, number>;
  D: Record<ShotChannel, number>;
  window_matches: number;
  n_eff: number;
  last_lineup: {
    match_id: number;
    date: string;
    formation: string;
    formation_has_template: boolean;
    starters: LineupStarter[];
  } | null;
  squad: string[];
}

export interface PlayerParams {
  player_id: string;
  name_zh: string | null;
  name_en: string | null;
  shirt_number: string | null;
  main_position: PosGroup;
  position_source: string;
  position_unverified: boolean;
  minutes: number;
  a_p: number;
  npxg90: number | null;
  xa90: number | null;
  shots90: number | null;
  headers90: number | null;
  penalties_taken: number;
  penalties_scored: number;
  r_p: number;
  g_p: number | null;
}

export interface FixtureParams {
  match_id: number;
  league_id: number;
  kickoff_at_utc: string;
  home_team_id: number;
  away_team_id: number;
  status: string;
  final_score: [number, number] | null;
  ah: { line: number; home: number; away: number; observed_at: string } | null;
  ou: { line: number; over: number; under: number; observed_at: string } | null;
  market_lambda: { home: number; away: number } | null;
}

export interface SimParams {
  meta: { generated_at: string; model_version: string; uncalibrated: boolean };
  leagues: Record<string, LeagueParams>;
  formations: Record<string, { samples: number; slots: FormationSlot[] }>;
  teams: Record<string, TeamParams>;
  players: Record<string, PlayerParams>;
  fixtures: Record<string, FixtureParams>;
}
