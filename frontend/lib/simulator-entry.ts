/**
 * 比赛页"模拟这场比赛"按钮的显示条件与链接(docs/simulator-launch-plan.md §4.1)。
 * 条件:SIMULATOR_ENABLED === "1" 且五大联赛 且有精确开球时间并在未来。
 * 纯函数,不带 "use client"。
 */
import { FIVE_LEAGUE_IDS } from "@/lib/homepage";

type MatchLike = {
  match_id: number;
  league_id: number;
  status: string;
  kickoff_at_utc?: string | null;
  home: { team_id?: number | null };
  away: { team_id?: number | null };
};

export function simulatorHrefFor(
  m: MatchLike,
  enabled: string | undefined,
  now: Date,
): string | null {
  if (enabled !== "1") return null;
  if (!FIVE_LEAGUE_IDS.includes(m.league_id)) return null;
  if (m.status !== "NotStarted" || !m.kickoff_at_utc) return null;
  if (m.home.team_id == null || m.away.team_id == null) return null;
  const t = Date.parse(m.kickoff_at_utc);
  if (!Number.isFinite(t) || t <= now.getTime()) return null;
  return `/simulator?lg=${m.league_id}&h=${m.home.team_id}&a=${m.away.team_id}&fx=${m.match_id}`;
}
