// 模拟事件 → 累计 xG / xG 赛跑图输入。动画里的实时累计 xG 与结果页赛跑图都走这里,
// 保证两处数字同源(CLAUDE.md §11.3:图上的聚合数字必须与图上画的点同源)。

import type { XgRaceShot } from "@/components/matches/XgRaceChart";
import type { SimEvent } from "./engine";

/** 截至 uptoTick(含)的两队累计 xG。乌龙球没有 xG,不计入;门将失误机会按 xG 0.5 计入。 */
export function cumulativeXg(events: SimEvent[], uptoTick = Infinity): [number, number] {
  const out: [number, number] = [0, 0];
  for (const e of events) {
    if (e.tick > uptoTick || e.xg === undefined) continue;
    out[e.team] += e.xg;
  }
  return out;
}

/** 转成比赛页 XgRaceChart 接受的射门行。乌龙球记在受益方(is_home 按受益方),xG 为空,只画进球点。 */
export function toReportShots(events: SimEvent[], teamIds: [number, number], halfTimeTick: number): XgRaceShot[] {
  return events
    .filter((e) => e.kind === "shot" || e.kind === "goal")
    .map((e) => ({
      player_id: e.playerId ?? "",
      player_name: e.channel === "owngoal" ? `${e.playerName ?? ""}(乌龙)` : e.playerName ?? null,
      team_id: teamIds[e.team],
      is_home: e.team === 0,
      minute: e.minute,
      minute_added: e.added,
      period: e.tick < halfTimeTick ? "FirstHalf" : "SecondHalf",
      xg: e.xg ?? null,
      situation: e.channel ?? null,
      outcome: e.kind === "goal" ? "Goal" : "Miss",
      is_own_goal: e.channel === "owngoal",
      is_own_goal_inferred: false,
    }));
}
