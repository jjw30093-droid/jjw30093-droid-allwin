/**
 * 联赛球员象限图的视角定义与纯逻辑(2026-09-15)。对标 quadrantViews.ts,
 * 复用其中真正通用的纯函数(quadrantOf/outlierNames/toggleSelection/mean/
 * fmt/dirsOf/axisLabel/resolveSelectedTeams/resolveClickedKey——2026-09-15
 * 已把这些函数的类型参数放宽成结构类型/泛型,球队/球员两侧零改动共用),
 * 但取数逻辑(plotSet 等价物)不复用——球员指标来自 PlayerQuadrantRow.ratios,
 * 是完全不同的形状。
 *
 * 站长拍板的五个视角(v1):门将 / 防守贡献 / 进攻创造力 / 持球推进 /
 * 射门与终结。**位置选择器是比较基准,不是纯过滤器**——同一个视角在不同
 * 选中位置下,均值/象限归属/每象限前 10 全部在"当前选中位置"内部重算
 * (贝林厄姆这类射门威胁突出的中场,丢进全体球员池会被前锋压成平平无奇,
 * 只有放进中场池才会正确落到"射门威胁突出"的象限——这是站长在审方案时
 * 明确要求的设计,见 2026-09-15 会话记录)。因此"射门与终结"视角对中场
 * 同样开放,不锁死只给前锋。
 *
 * 2026-09-16 补第六个视角"门将出球"(传球成功率 × 长传占比,仅 2026/2027
 * 起有数据)——同时补上每个视角的 `positions` 位置限定字段。这不是推翻
 * 上一条"位置=比较基准"的原则,是补上它没覆盖的反方向:那条原则解决的是
 * "别把射门与终结锁死只给前锋",没考虑"有些视角对门将这个位置从概念上
 * 就不该出现"——门将也会有触球/回收球一类的零星数据,足够凑够 4 人把
 * "防守贡献""持球推进"这类视角的数据门槛喂饱,但拿这些数据比较门将毫无
 * 产品意义(真实反馈,站长发现后要求修正)。据此固定:非门将视角只对
 * 后卫/中场/前锋开放,门将专属视角只对门将开放,两者不重叠。
 *
 * 每个象限只画离均值最远的 10 人(站长拍板)——outlierNames 的"离均值距离"
 * 排序同款逻辑,按象限分组后各取前 10。均值仍由**全部达标球员**计算,
 * 不是只由画出来的 10 人计算(图下必须说明这一点,不能让虚线的含义随
 * "画了几个人"变化)。
 *
 * 诚实纪律:出场占比门槛(40%)未达标的球员在 playerPlotSet 里被排除,
 * 像 quadrantViews.ts::plotSet 一样如实计数"藏了几个、为什么"。
 */

import type { PlayerQuadrantRow } from "@/lib/api-v1";
import { playerAvatarUrl } from "@/components/players/playerAvatarUrl";
import {
  meetsMinutesShare,
  playerKey,
  PLAYER_METRICS,
  type PlayerMetricDef,
  type PositionValue,
} from "./playerMetrics";
import {
  axisLabel,
  dirsOf,
  fmt,
  mean,
  quadrantOf,
  resolveClickedKey,
  resolveSelectedTeams,
  toggleSelection,
  type Dirs,
} from "./quadrantViews";

export { playerKey, axisLabel, dirsOf, fmt, mean, quadrantOf, resolveClickedKey, toggleSelection };
export { resolveSelectedTeams as resolveSelectedPlayers };

export type PlayerView = {
  id: string;
  tab: string;
  title: string;
  x: PlayerMetricDef;
  y: PlayerMetricDef;
  /** 四象限中文名,顺序按好/差:x好y好 / x差y好 / x差y差 / x好y差
   *  (同 quadrantViews.ts::quadrantOf 的既有约定)。 */
  quadrants: [string, string, string, string];
  note: string;
  /** 这个视角在哪些位置下可选——不是数据门槛(那是 playerPlotSet 的
   *  minutes_share 40% 校验),是"这个组合有没有产品意义"的硬性限定。
   *  门将专属视角只列 [0],非门将视角只列 [1,2,3],两者互不相交。 */
  positions: readonly PositionValue[];
};

/** 按 id 取视角,找不到直接抛错(不允许静默回退)。 */
export function playerViewById(id: string): PlayerView {
  const v = PLAYER_VIEWS.find((x) => x.id === id);
  if (!v) throw new Error(`未知球员视角 id: ${id}`);
  return v;
}

export const PLAYER_VIEWS: PlayerView[] = [
  {
    id: "player-shooting-finishing",
    tab: "射门与终结",
    title: "每90分钟非点球xG × 终结超额",
    x: PLAYER_METRICS.npxgPer90,
    y: PLAYER_METRICS.finishingDeltaPer90,
    quadrants: ["机会多且效率高", "机会少但效率惊人", "机会少且效率不足", "机会多但效率不足"],
    note: "横轴是每 90 分钟制造的非点球预期进球(机会质量与数量),纵轴是场均终结超额(实际进球减去预期进球)——纵轴是短期窗口的结果记录,不是稳定的终结能力,调研认为这类差值跨赛季相关性接近零。这个视角对中场同样开放:射门威胁突出的中场(如常被拿来举例的「本菲卡/皇马式」攻击型中场)只有放进中场自己的比较池里,才能正确显示出这份威胁,丢进全联赛混合池会被前锋的量级压平。",
    // 后卫/中场/前锋都开放(不只锁中场/前锋)——本次修正只处理"门将不该
    // 看这批非门将视角"这一件事,不额外收紧非门将位置之间的既有开放度。
    positions: [1, 2, 3],
  },
  {
    id: "player-creativity",
    tab: "进攻创造力",
    title: "每90分钟创造机会数 × 预期助攻(xA)",
    x: PLAYER_METRICS.chancesCreatedPer90,
    y: PLAYER_METRICS.xaPer90,
    quadrants: ["量质俱佳", "少而精", "创造有限", "机会多但质量不足"],
    note: "横轴是每 90 分钟创造的射门机会次数,纵轴是预期助攻(创造的机会按转化概率折算)——只有球员维度才有 xA 这个字段,球队维度算不出来。右下角「机会多但质量不足」是创造次数不少、但机会平均含金量偏低的球员。",
    positions: [1, 2, 3],
  },
  {
    id: "player-defensive-contribution",
    tab: "防守贡献",
    title: "每90分钟防守动作(CBIRT) × 对抗成功率",
    x: PLAYER_METRICS.defensiveActionsPer90,
    y: PLAYER_METRICS.duelWinRate,
    quadrants: ["高产且高效", "动作不多但赢得多", "参与有限", "动作多但成功率不高"],
    note: "横轴是每 90 分钟的抢断+拦截+解围+封堵+回收球——行业标准 Defensive Contribution 口径(FPL/Opta 的 CBIRT),不是本站发明的代理指标。纵轴是全部对抗(地面+空中)里赢下的比例。两轴都是可比优劣的表现指标,但数字高不直接等于「防守好」,也要结合位置与球队整体防守策略一起看。",
    positions: [1, 2, 3],
  },
  {
    id: "player-progression",
    tab: "持球推进",
    title: "每90分钟触球数 × 每百次触球送进前场传球数",
    x: PLAYER_METRICS.touchesPer90,
    y: PLAYER_METRICS.progressionRate,
    quadrants: ["高产且高效推进", "触球不多但效率高", "推进乏力", "触球多但效率不足"],
    note: "横轴是每 90 分钟触球次数,纵轴是每百次触球里有多少次传球送进了前场——两轴都是打法特征,不是强弱评价。触球数同时是纵轴的分母,数字有轻度机械相关(触球基数越大,单次「送进前场」的比例天然更容易被稀释),读图时留意。",
    positions: [1, 2, 3],
  },
  {
    id: "player-goalkeeping",
    tab: "门将",
    title: "每90分钟面对射正预期进球 × 扑救超额",
    x: PLAYER_METRICS.xgotFacedPer90,
    y: PLAYER_METRICS.goalsPreventedPer90,
    quadrants: ["低压力且高产出", "低压力但产出一般", "高压力且吃紧", "高压力下站得住"],
    note: "横轴是每 90 分钟面对的射正预期进球(承压程度,已反转,越靠左说明球队防线把对手逼到的射门位置越差),纵轴是扑救超额(面对的预期进球减去实际失球)——纵轴是短期窗口的结果记录,不是稳定的门将能力评价,不代表未来表现。",
    positions: [0],
  },
  {
    id: "player-goalkeeper-distribution",
    tab: "门将出球",
    title: "传球成功率 × 长传占比",
    x: PLAYER_METRICS.passCompletionRate,
    y: PLAYER_METRICS.longBallShare,
    quadrants: ["短传精准且常出长传", "短传精准但少出长传", "短传欠精准且少出长传", "短传欠精准但常出长传"],
    note: "横轴是传球成功率,纵轴是长传占全部传球的比例——两轴共用同一个分母(传球尝试总数),不会出现两种口径打架。这是打法特征(短传出球型 vs 长传解围型),不是强弱评价。仅 2026/2027 起的赛季有数据:传球尝试总数这个字段更早的赛季历史上几乎不下发。",
    positions: [0],
  },
];

export type PlayerPt = {
  key: string;
  name: string;
  x: number;
  y: number;
  /** 出场场次(appearances),tooltip 里显示"样本 N 场"。 */
  mp: number | null;
  avatarUrl: string | null;
  playerId: string | null;
  teamName: string;
  teamCrestUrl: string | null;
};

export type PlayerHiddenEntry = {
  key: string;
  name: string;
  axis: "x" | "y";
  /** missing = 该指标数据源没给;below_threshold = 出场占比低于门槛(未达 40%) */
  reason: "missing" | "below_threshold";
};

export type PlayerPlotSet = { pts: PlayerPt[]; hidden: PlayerHiddenEntry[] };

/** 与 quadrantViews.ts::plotSet 同一节奏,但门槛判定是单一的
 *  meetsMinutesShare(不是每指标各自的 SampleRule)——一旦某球员出场占比
 *  不达标,该球员在**所有视角**里都不画,不是逐指标各自判断。 */
export function playerPlotSet(rows: PlayerQuadrantRow[], view: PlayerView): PlayerPlotSet {
  const pts: PlayerPt[] = [];
  const plotted = new Set<string>();
  const hiddenByKey = new Map<string, PlayerHiddenEntry>();
  for (const r of rows) {
    const key = playerKey(r.player);
    if (plotted.has(key)) continue;
    const name = r.player.name;
    if (!meetsMinutesShare(r)) {
      hiddenByKey.set(key, { key, name, axis: "x", reason: "below_threshold" });
      continue;
    }
    const x = view.x.value(r);
    const y = view.y.value(r);
    if (x == null || y == null) {
      hiddenByKey.set(key, { key, name, axis: x == null ? "x" : "y", reason: "missing" });
      continue;
    }
    plotted.add(key);
    hiddenByKey.delete(key);
    pts.push({
      key,
      name,
      x,
      y,
      mp: r.appearances ?? null,
      avatarUrl: r.player.player_id ? playerAvatarUrl(r.player.player_id) : null,
      playerId: r.player.player_id ?? null,
      teamName: r.team.name,
      teamCrestUrl: r.team.crest_url ?? null,
    });
  }
  return { pts, hidden: [...hiddenByKey.values()] };
}

/** 藏了几个人、为什么——图下脚注文案,与 quadrantViews.ts::hiddenNote 同一
 *  产物形状但措辞独立(门槛机制不同,不能共用同一句话模板)。 */
export function playerHiddenNote(hidden: PlayerHiddenEntry[]): string {
  const names = (hs: PlayerHiddenEntry[]) => hs.map((h) => h.name).join("、");
  const byThreshold = hidden.filter((h) => h.reason === "below_threshold");
  const byMissing = hidden.filter((h) => h.reason === "missing");
  const out: string[] = [];
  if (byThreshold.length) {
    out.push(
      `另有 ${byThreshold.length} 名球员出场时间不足本队已踢时间的 40% 未画出：${names(byThreshold)}`,
    );
  }
  if (byMissing.length) {
    out.push(`${byMissing.length} 名球员数据源缺这两项之一，同样未画出：${names(byMissing)}`);
  }
  if (!out.length) return "";
  return out.join("；") + "。虚线平均值只统计画出的球员。";
}

export const PLAYERS_PER_QUADRANT = 10;

/**
 * 每个象限只画离均值最远的 10 人(站长拍板)。均值(mx/my)必须由调用方
 * 传入**全部达标球员**算出的均值(不是只由这 10 人算),这条不变量由
 * playerPlotSet → topPerQuadrant 的调用顺序保证:先用 plotSet 的全部 pts
 * 算 mx/my,再用 topPerQuadrant 从这批 pts 里各象限挑 10 个来画。
 *
 * 复用 outlierNames 同一套"两轴标准差归一化后按离均值距离排序"算法,
 * 只是这里按象限分组后各自独立排序、各取前 N,不是全局取一批。
 */
export function topPerQuadrant(
  pts: PlayerPt[],
  mx: number,
  my: number,
  dirs: Dirs,
  take = PLAYERS_PER_QUADRANT,
): { drawn: PlayerPt[]; totalByQuadrant: number[] } {
  const byQuadrant: PlayerPt[][] = [[], [], [], []];
  for (const p of pts) byQuadrant[quadrantOf(p, mx, my, dirs)].push(p);

  const sd = (vals: number[], m: number) =>
    Math.sqrt(vals.reduce((a, v) => a + (v - m) ** 2, 0) / (vals.length || 1)) || 1;
  const sx = sd(pts.map((p) => p.x), mx);
  const sy = sd(pts.map((p) => p.y), my);
  const dist2 = (p: PlayerPt) => ((p.x - mx) / sx) ** 2 + ((p.y - my) / sy) ** 2;

  const drawn: PlayerPt[] = [];
  const totalByQuadrant = byQuadrant.map((group) => group.length);
  for (const group of byQuadrant) {
    const sorted = [...group].sort((a, b) => dist2(b) - dist2(a));
    drawn.push(...sorted.slice(0, take));
  }
  return { drawn, totalByQuadrant };
}

/** 每象限截断的披露文案——与 playerHiddenNote 是两种不同的"没画出来"原因
 *  (那个是样本不达标,这个是象限内人太多只挑了最极端的 N 个),文案不能
 *  混为一谈(CLAUDE.md 禁止静默截断)。 */
export function quadrantTruncationNote(totalByQuadrant: number[], take = PLAYERS_PER_QUADRANT): string {
  const truncated = totalByQuadrant.filter((n) => n > take);
  if (!truncated.length) return "";
  return `每象限只画离均值最远的 ${take} 人；虚线均值统计全部达标球员（不受这条截断影响）。`;
}
