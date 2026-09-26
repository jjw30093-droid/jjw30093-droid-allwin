/**
 * 球员榜(纯展示)。消费 /api/v1/leagues/{id}/players 的 28 个维度
 * (服务端已完成中文名解析、按显示值重排名次、top20 截取,并标好正/负榜)。
 *
 * 2026-09-26:按 重点数据/进攻/防守/纪律 分组渲染(见 playerBoardGroups.ts),
 * 顶部加吸顶分组条;每张榜默认显示前 3 名,点「查看全部」展开到 10 名
 * (LeaderboardCard 的通用行为)。
 */

import { LeaderboardCard, type LeaderboardRow } from "@/components/LeaderboardCard";
import type { PlayersResponse } from "@/lib/api-v1";
import { BoardSectionTabs } from "./BoardSectionTabs";
import { boardSectionDomId } from "./boardSections";
import {
  PLAYER_GROUP_ORDER,
  PLAYER_GROUP_TITLES,
  playerGroupOf,
  type PlayerBoardGroupKey,
} from "./playerBoardGroups";
import styles from "./boards.module.css";

const INTEGER_STATS = new Set(["goals", "goal_assist"]);

function formatValue(statKey: string, value: number | null | undefined): string {
  if (value == null) return "—";
  return INTEGER_STATS.has(statKey) ? String(Math.round(value)) : value.toFixed(2);
}

export function PlayerBoards({ boards }: { boards: PlayersResponse["boards"] }) {
  const byGroup = new Map<PlayerBoardGroupKey, PlayersResponse["boards"]>(
    PLAYER_GROUP_ORDER.map((k) => [k, []]),
  );
  for (const board of boards) byGroup.get(playerGroupOf(board.stat_name))!.push(board);
  const present = PLAYER_GROUP_ORDER.filter((k) => byGroup.get(k)!.length > 0);

  return (
    <div>
      <BoardSectionTabs sections={present.map((k) => ({ id: k, label: PLAYER_GROUP_TITLES[k] }))} />
      {present.map((key) => (
        <section
          key={key}
          id={boardSectionDomId(key)}
          className={styles.sectionPlain}
          aria-label={PLAYER_GROUP_TITLES[key]}
        >
          <div className={styles.grid}>
            {byGroup.get(key)!.map((board) => {
              const rows: LeaderboardRow[] = board.entries.map((e, i) => ({
                rank: e.rank ?? i + 1,
                name: e.name,
                subtitle: e.team.name,
                value: formatValue(board.stat_name, e.value),
                avatar: { kind: "player", playerId: e.player_id },
                // 球员榜的榜首胶囊用他所属球队的队色(FotMob 同款)
                teamColor: e.team_color,
              }));
              return (
                <LeaderboardCard
                  key={board.stat_name}
                  title={board.label_zh}
                  rows={rows}
                  highIsBad={board.direction === "high_bad"}
                />
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
