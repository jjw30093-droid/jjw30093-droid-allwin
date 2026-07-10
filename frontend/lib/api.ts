// 前端只读 serving API,不直连 DB(CLAUDE.md §2)。API base 走环境变量,不写死。
const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

export interface StandingRow {
  position: number;
  played: number;
  wins: number;
  draws: number;
  losses: number;
  goals_for: number;
  goals_against: number;
  goal_diff: number;
  points: number;
  qual_color: string | null;
  team_id: number;
  team_name_zh: string | null;
}

// 免费字段(CLAUDE.md §3):射门/射正/控球/xG/xGOT。角球/黄红牌/零封/BTTS
// 是付费字段,不在 overview 里返回,这里也就没有对应的 TS 字段——不是漏写。
export interface TeamStatsRow {
  team_id: number;
  team_name_zh: string | null;
  matches_played: number;
  avg_total_shots: number | null;
  avg_shots_on_target: number | null;
  avg_possession: number | null;
  avg_expected_goals: number | null;
  avg_expected_goals_on_target: number | null;
}

export interface PlayerLeaderboardEntry {
  player_id: string;
  Player_Name: string | null;
  player_name_zh: string | null;
  player_name_zh_short: string | null;
  Team_ID: number | null;
  Team_Name: string | null;
  team_name_zh: string | null;
  rank: number;
  value: number;
}

export interface PlayerLeaderboard {
  label_zh: string;
  entries: PlayerLeaderboardEntry[];
}

export interface LeagueOverview {
  league_id: number;
  season: string;
  standings: StandingRow[];
  team_stats: TeamStatsRow[];
  player_leaderboards: Record<string, PlayerLeaderboard>;
}

export async function fetchLeagueOverview(
  leagueId: string,
  season?: string
): Promise<LeagueOverview> {
  const url = new URL(`/api/league/${leagueId}/overview`, API_BASE);
  if (season) url.searchParams.set("season", season);

  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`serving API ${res.status}: ${body}`);
  }
  return res.json();
}

// 赛程/结果(免费层)。只有对阵/比分/日期/轮次/status,不含任何概率字段
// ——概率是付费概率卡页(wdl-predictions)的事,这里没有、也不该有。
export interface MatchRow {
  Match_ID: number;
  Date: string;
  home_score: number | null;
  away_score: number | null;
  status: string;
  Match_Round: string;
  home_team_id: number;
  away_team_id: number;
  home_team_name_zh: string | null;
  away_team_name_zh: string | null;
}

export interface LeagueMatchesResponse {
  league_id: number;
  season: string;
  matches: MatchRow[];
}

export async function fetchLeagueMatches(
  leagueId: string,
  season?: string
): Promise<LeagueMatchesResponse> {
  const url = new URL(`/api/league/${leagueId}/matches`, API_BASE);
  if (season) url.searchParams.set("season", season);

  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`serving API ${res.status}: ${body}`);
  }
  return res.json();
}

// WDL 概率卡(付费核心,CLAUDE.md §3)。两层门禁,互相独立:
//   1. availability:'upcoming'(距开赛 >7 天)时,tendency/confidence/
//      reason/locked/p_home/p_draw/p_away 这些字段全部不存在——不是付费
//      判断管的事,时候没到,谁都看不到。
//   2. availability:'live'(距开赛 ≤7 天)时才谈得上付费 gate:
//      locked=true 时 p_home/p_draw/p_away 根本不下发(不是下发了再靠
//      前端隐藏)。
// 因此这些字段在 TypeScript 里都是可选的,按 availability 分支读取。
export type Tendency = "home" | "draw" | "away";
export type Confidence = "normal" | "low";
export type Availability = "live" | "upcoming";

export interface WdlMatch {
  match_id: number;
  date: string | null;
  round: string | null;
  status: string;
  home_team_id: number;
  away_team_id: number;
  home_team_name_zh: string | null;
  away_team_name_zh: string | null;
  availability: Availability;
  days_until_kickoff: number | null;
  tendency?: Tendency | null;
  confidence?: Confidence | null;
  reason?: string | null;
  locked?: boolean;
  p_home?: number;
  p_draw?: number;
  p_away?: number;
}

export interface WdlPredictionsResponse {
  league_id: number;
  season: string;
  matches: WdlMatch[];
}

export async function fetchWdlPredictions(
  leagueId: string,
  season?: string,
  simulateMembership?: "paid"
): Promise<WdlPredictionsResponse> {
  const url = new URL(`/api/league/${leagueId}/wdl-predictions`, API_BASE);
  if (season) url.searchParams.set("season", season);
  if (simulateMembership) url.searchParams.set("simulate_membership", simulateMembership);

  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`serving API ${res.status}: ${body}`);
  }
  return res.json();
}
