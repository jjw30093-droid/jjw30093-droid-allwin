/**
 * 球员榜(纯展示)。消费 /api/v1/leagues/{id}/players 的 5 个免费维度
 * (进球/助攻/xG/xGOT/评分,服务端已完成中文名解析与 top10 截取)。
 */

import { LeaderboardCard, type LeaderboardRow } from "@/components/LeaderboardCard";
import type { PlayersResponse } from "@/lib/api-v1";
import styles from "./boards.module.css";

const INTEGER_STATS = new Set(["goals", "goal_assist"]);

function formatValue(statKey: string, value: number | null | undefined): string {
  if (value == null) return "—";
  return INTEGER_STATS.has(statKey) ? String(Math.round(value)) : value.toFixed(2);
}

export function PlayerBoards({ boards }: { boards: PlayersResponse["boards"] }) {
  return (
    <div className={styles.grid}>
      {boards.map((board) => {
        const rows: LeaderboardRow[] = board.entries.map((e, i) => ({
          rank: e.rank ?? i + 1,
          name: e.name,
          subtitle: e.team.name,
          value: formatValue(board.stat_name, e.value),
        }));
        return (
          <LeaderboardCard key={board.stat_name} title={board.label_zh} rows={rows} />
        );
      })}
    </div>
  );
}
