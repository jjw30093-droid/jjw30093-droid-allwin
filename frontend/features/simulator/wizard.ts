// 三步向导(主队 → 客队 → 模拟)的纯逻辑:初始步骤、换队时的撞队处理、阵容完整性提示。
// 无 "use client":page.tsx(服务端)与 SimulatorClient(客户端)都从这里 import。

import type { TeamSetup } from "./engine";
import { parseSetupQuery, parseTeamsQuery } from "./shareLink";
import type { SimParams } from "./types";

export type WizardStep = 1 | 2;

/** 链接里带了本联赛的两支不同球队(完整分享设定或只带两队)→ 直接进第 2 步;否则第 1 步。
 *  与 SimulatorClient 挂载时的恢复逻辑同一判据,服务端先算好,页面不会先闪第 1 步。 */
export function stepForQuery(search: string, params: SimParams, leagueId: number): WizardStep {
  const full = parseSetupQuery(search);
  const pair = full ? { lg: full.leagueId, h: full.home.teamId, a: full.away.teamId } : (() => {
    const t = parseTeamsQuery(search);
    return t ? { lg: t.leagueId, h: t.home, a: t.away } : null;
  })();
  if (!pair) return 1;
  const ok = pair.lg === leagueId && pair.h !== pair.a && !!params.teams[String(pair.h)] && !!params.teams[String(pair.a)];
  return ok ? 2 : 1;
}

function firstOther(teamIds: number[], avoid: number): number {
  return teamIds.find((id) => id !== avoid) ?? avoid;
}

/** 换主队:客队与新主队相同时,客队改成列表里第一支不同的队。返回 [主, 客]。 */
export function pairWithHome(pair: [number, number], h: number, teamIds: number[]): [number, number] {
  return [h, pair[1] === h ? firstOther(teamIds, h) : pair[1]];
}

/** 换客队:同上,对称。 */
export function pairWithAway(pair: [number, number], a: number, teamIds: number[]): [number, number] {
  return [pair[0] === a ? firstOther(teamIds, a) : pair[0], a];
}

/** 阵容完整性(与 engine.prepareMatch 的三条首发校验同口径,tests/simulator-wizard.test.ts 对拍);null = 可以进下一步。 */
export function lineupIssue(params: SimParams, setup: TeamSetup): string | null {
  const empty = setup.slots.filter((s) => !s.playerId).length;
  if (empty > 0) return `请先补全阵容:还有 ${empty} 个位置没有球员`;
  const ids = setup.slots.map((s) => s.playerId as string);
  if (new Set(ids).size !== ids.length) return "首发里有重复球员";
  if (!ids.some((id) => params.players[id]?.main_position === "GK")) return "首发中没有门将";
  return null;
}

/** 空位下标(用于在球场上标出来)。 */
export function emptySlots(setup: TeamSetup): number[] {
  return setup.slots.map((s, i) => (s.playerId ? -1 : i)).filter((i) => i >= 0);
}

/** 第 2 步「预计进球」卡片里的盘口状态:无论是否匹配都显示一行,免得用户以为用上了盘口。
 *  fixtureId = 当前实际参考的比赛(关掉参考时为 null);pairHasFixture = 这对主客队在参数里有没有带盘口的比赛。 */
export function marketStatus(params: SimParams, fixtureId: number | null, pairHasFixture: boolean): string {
  if (fixtureId != null) {
    return params.fixtures[String(fixtureId)]?.status === "未开赛"
      ? "已参考本场盘口(未开赛)"
      : "已参考本场赛前盘口(比赛已踢完)";
  }
  if (pairHasFixture) return "本场有盘口,当前未参考,只用数据模型";
  return "这对球队近期没有盘口数据,只用数据模型";
}
