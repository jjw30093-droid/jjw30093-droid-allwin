/**
 * 推荐单在列表里显示的"联赛"标签(2026-10-01 站长 P0)。
 *
 * 推荐单标题是后台人工自由文本,同一个联赛出现过「荷兰甲 / 荷甲 / 荷甲早场」
 * 「韩k / 韩k精选」「日职 / 日职联 / 周五早场日职联」等写法;列表直接显示标题,
 * 用户会以为是不同联赛。这里优先用后端按 match_id 派生的标准联赛名
 * (腿上的 league_name_zh),多个联赛用「·」连接;一条都取不到(站外赛事)
 * 才退回原标题,不编造。
 *
 * 纯函数,不带 "use client"。
 */
type LegLike = { league_name_zh?: string | null };

export function slipLeagueLabel(slip: { title: string; legs: ReadonlyArray<LegLike> }): string {
  const names: string[] = [];
  for (const leg of slip.legs) {
    const n = leg.league_name_zh;
    if (n && !names.includes(n)) names.push(n);
  }
  return names.length > 0 ? names.join(" · ") : slip.title;
}
