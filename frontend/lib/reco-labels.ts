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

/**
 * 推荐单每条腿的玩法与选项,改成直白说法(2026-10-02 站长 P1:不加解释,只把行话换成人话)。
 * - 「主队/客队」换成真实队名(从 match_desc「A vs B MM/DD HH:MM」取);
 * - 「1.0」这种整数线去掉「.0」;玩法名缩短:亚洲让球→让球、大小球→进球数、角球大小→角球数、
 *   胜平负(欧赔)→胜平负。
 * 认不出的格式原样返回,不猜。
 */
const PICK_MARKET_ZH: Record<string, string> = {
  "1x2": "胜平负",
  ah: "让球",
  ou: "进球数",
  corners_ou: "角球数",
};

export function teamsFromDesc(desc: string): { home: string; away: string } | null {
  const m = desc.match(/^(.+?)\s+vs\s+(.+?)(?:\s+\d{1,2}[/-]\d{1,2}\s+\d{1,2}:\d{2})?\s*$/);
  return m ? { home: m[1].trim(), away: m[2].trim() } : null;
}

function trimLine(v: string): string {
  return v.replace(/\.0+$/, "");
}

export function legPick(leg: { market: string; selection: string; match_desc: string }): {
  market: string;
  selection: string;
} {
  const market = PICK_MARKET_ZH[leg.market] ?? leg.market;
  const teams = teamsFromDesc(leg.match_desc);
  const sel = leg.selection.trim();
  let out: string | null = null;
  if (leg.market === "ah" && teams) {
    const m = sel.match(/^(主队|客队)(受让|让)([\d.]+)球$/);
    if (m) out = `${m[1] === "主队" ? teams.home : teams.away} ${m[2]} ${trimLine(m[3])} 球`;
  } else if (leg.market === "1x2" && teams) {
    if (sel === "主胜") out = `${teams.home} 胜`;
    else if (sel === "客胜") out = `${teams.away} 胜`;
    else if (sel === "平局" || sel === "平") out = "平局";
  } else if (leg.market === "ou") {
    const m = sel.match(/^(大|小)([\d.]+)$/);
    if (m) out = `${m[1]} ${trimLine(m[2])} 球`;
  } else if (leg.market === "corners_ou") {
    const m = sel.match(/^(大|小)角球([\d.]+)$/);
    if (m) out = `${m[1]} ${trimLine(m[2])} 个`;
  }
  return { market, selection: out ?? sel };
}


/** "20中 11黑 2走"(四分之一盘半赢/半输只在出现时显示)。精选页与战绩页共用:
 * 首页战绩卡写"31 单 20 中"(只算分出输赢的单),这里把走水单独列出来,
 * 两处能对上(31 + 2 = 33)。 */
export function resultTallyText(s: {
  win_count: number; lose_count: number; push_count: number;
  half_win_count: number; half_loss_count: number;
}): string {
  const parts = [`${s.win_count}中`];
  if (s.half_win_count > 0) parts.push(`${s.half_win_count}半赢`);
  parts.push(`${s.lose_count}黑`);
  if (s.half_loss_count > 0) parts.push(`${s.half_loss_count}半输`);
  parts.push(`${s.push_count}走`);
  return parts.join(" ");
}
