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
  stoppage_distribution: { first_half: Record<string, number>; second_half: Record<string, number> };
  gk_xgot_faced_per90: number;
  /** 主场系数 h:主队 ×h、客队 ×1/h(Phase 2 校准)。缺省时用 v0.2 的 1.08 / 0.93。 */
  home_advantage?: number;
  /** 联赛场均进球 / 场均非乌龙 xG(每队每场;v0.3 强度回归的 μ) */
  goals_per_team_match?: number;
  xg_per_team_match?: number;
}

/** v0.3 第 7 条:强度回归特征。A / D 为 [k=0, k=5] 两个收缩版本。 */
export interface StrengthFeature {
  n: number;
  n_eff: number;
  xg: { A: [number, number]; D: [number, number] };
  goals: { A: [number, number]; D: [number, number] };
}

/** v0.3 第 7 条:数据模型的强度回归(log λ = α + γ·log(强度/μ) + β_home·主场)。 */
export interface StrengthModel {
  window: "10" | "20" | "38";
  basis: "xg" | "goals" | "both";
  k: 0 | 5;
  alpha: number;
  gamma: { xg_A?: number; xg_D?: number; goals_A?: number; goals_D?: number };
  beta_home: number;
}

/** 比分状态乘数(§7.2):落后 1 球 open / counter,领先 1 球、落后 ≥2、领先 ≥2 作用于全部渠道。 */
export interface StateMultipliers {
  trail1_open: number;
  trail1_counter: number;
  lead1_all: number;
  trail2_all: number;
  lead2_all: number;
}

/** v0.3:时段 / 比分状态 / 红牌的 Poisson 回归系数(对数乘数,相对 λ/90)。 */
export interface RateModel {
  /** 1–15、16–30、31–45+、46–60、61–75 五个时段(持平状态) */
  periods: [number, number, number, number, number];
  /** 75 分钟前的比分状态(参照持平) */
  early_state: { trail2: number; trail1: number; lead1: number; lead2: number };
  /** 75 分钟后(含下半场补时)按比分状态的格子 */
  late: { level: number; trail1: number; trail2: number; lead1: number; lead2: number };
  red_own_down: number;
  red_opp_down: number;
}

/** Phase 2 校准得到的参数;缺省为 v0.2 取值。 */
export interface Calibration {
  market_w: number;
  /** 0 = 关闭状态系数(ε ≡ 1) */
  kappa: number;
  rate_model: RateModel;
  strength_model: StrengthModel;
  /** 合并主场系数(联赛未单独给 home_advantage 时使用) */
  home_advantage: number;
  red_own: number;
  red_opp: number;
  state: StateMultipliers;
}

// 位置分组不在模板里重复存:一律查 SimParams.position_map(单一映射表,规格 v0.2 第 5 条)。
export interface FormationSlot {
  position_id: number;
  x: number | null;
  y: number | null;
}

export interface LineupStarter {
  player_id: string;
  position_id: number;
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
    formation_in_position_map: boolean;
    starters: LineupStarter[];
  } | null;
  squad: string[];
  strength?: Record<string, StrengthFeature>;
}

export interface PlayerParams {
  player_id: string;
  name_zh: string | null;
  name_en: string | null;
  shirt_number: string | null;
  team_id: number;
  usual_position_id: number | null;
  top_formation: string | null;
  top_position_id: number | null;
  top_position_starts: number;
  starts: number;
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
  meta: { generated_at: string; effective_version?: string; model_version: string; uncalibrated: boolean };
  leagues: Record<string, LeagueParams>;
  formations: Record<string, { samples: number; slots: FormationSlot[] }>;
  position_map: Record<string, Record<string, PosGroup>>;
  calibration?: Partial<Calibration>;
  teams: Record<string, TeamParams>;
  players: Record<string, PlayerParams>;
  fixtures: Record<string, FixtureParams>;
}
