// Phase 2 回测输入(scripts/simulator/backtest_snapshots.py 在服务器上生成,拉到 .local-data 使用)。

import type { LeagueParams, PlayerParams, PosGroup, ShotChannel, TeamParams } from "../types";

export interface SnapshotTeam extends TeamParams {
  A_by_k: Record<string, Record<ShotChannel, number>>;
  D_by_k: Record<string, Record<ShotChannel, number>>;
  xg10: { for: number | null; against: number | null; n: number };
}

export interface SnapshotLineup {
  formation: string;
  starters: { player_id: string; position_id: number; group: PosGroup }[];
}

export interface Prematch {
  match_id: number;
  league_id: number;
  round: number | null;
  date: string;
  kickoff_at_utc: string | null;
  home_team_id: number;
  away_team_id: number;
  lineups: { home: SnapshotLineup | null; away: SnapshotLineup | null };
  crown: {
    ah: { line: number; home: number; away: number; observed_at: string } | null;
    ou: { line: number; over: number; under: number; observed_at: string } | null;
    market_lambda: { home: number; away: number } | null;
  } | null;
}

export interface Snapshot {
  date: string;
  params: {
    leagues: Record<string, LeagueParams>;
    formations: Record<string, { samples: number; slots: { position_id: number; x: number | null; y: number | null }[] }>;
    position_map: Record<string, Record<string, PosGroup>>;
    teams: Record<string, SnapshotTeam>;
    players: Record<string, PlayerParams>;
  };
  prematch: Prematch[];
}

export interface Outcome {
  match_id: number;
  league_id: number;
  round: number | null;
  date: string;
  home_team_id: number;
  away_team_id: number;
  score: [number, number];
  goals: { minute: number; added: number; team: 0 | 1; own_goal: boolean; channel: string }[];
  goals_consistent: boolean;
  reds: { minute: number; added: number; team: 0 | 1 }[];
  penalty_attempts: [number, number];
  stoppage_announced: [number | null, number | null];
  stoppage_observed_max: [number, number];
}
