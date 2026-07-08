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

export interface LeagueOverview {
  league_id: number;
  season: string;
  standings: StandingRow[];
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
