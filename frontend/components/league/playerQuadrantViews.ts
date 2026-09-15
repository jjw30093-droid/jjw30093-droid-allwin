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
 * 产品意义(真实反馈,站长发现后要求修正)。
 *
 * 2026-09-16 二次修正(站长发现后卫/中场/前锋看到的 tab 一模一样):上面那
 * 次加 `positions` 时只挡了门将,把四个非门将视角一律写成 [1,2,3],等于
 * 把方案表里的「主场景位置」一列丢了。**「位置=比较基准」说的是"同一张图
 * 在不同位置下要各自重算均值",不是"每张图都要对所有位置开放"**,两件事
 * 被我混成了一件。现按方案表恢复(唯一一处偏离由站长当场拍板):
 *   后卫 [1] → 防守贡献 / 持球推进 / 进攻创造力
 *   中场 [2] → 四个全开(中场本来就是每一张图的主场景之一)
 *   前锋 [3] → 射门与终结 / 进攻创造力
 * 后卫的「进攻创造力」是站长加的:usual_position 只有四档,边后卫和中卫
 * 混在「后卫」里,而边后卫恰恰是传中/创造机会/xA 的主力,砍掉这张图会让
 * 边后卫最有价值的一面整个消失。门将专属视角仍只对门将开放,与非门将视角
 * 不重叠。
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
    quadrants: ["状态火热", "一剑封喉", "锋线哑火", "屡失良机"],
    note: "横轴是每 90 分钟制造的非点球预期进球(机会质量与数量),纵轴是场均终结超额(实际进球减去预期进球)——纵轴是短期窗口的结果记录,不是稳定的终结能力,调研认为这类差值跨赛季相关性接近零。这个视角对中场同样开放:像贝林厄姆这类射门威胁突出的攻击型中场,只有放进中场自己的比较池里,才能正确显示出这份威胁,丢进全联赛混合池会被前锋的量级压平。",
    // 中场/前锋(计划表「主场景位置」)。中场必须开放:射门威胁突出的攻击型
    // 中场只有放进中场自己的比较池里才看得出来,这正是"位置=比较基准"要
    // 解决的事;后卫不开放——中卫的射门样本基本来自定位球头球,放进同一张
    // 图只会制造噪声。
    positions: [2, 3],
  },
  {
    id: "player-creativity",
    tab: "进攻创造力",
    title: "每90分钟创造机会数 × 预期助攻(xA)",
    x: PLAYER_METRICS.chancesCreatedPer90,
    y: PLAYER_METRICS.xaPer90,
    quadrants: ["量质俱佳", "少而精", "创造有限", "多而不精"],
    note: "横轴是每 90 分钟创造的射门机会次数,纵轴是预期助攻(创造的机会按转化概率折算)——只有球员维度才有 xA 这个字段,球队维度算不出来。右下角「多而不精」是创造次数不少、但机会平均含金量偏低的球员。",
    // 计划表「主场景位置」是中场/前锋,后卫是站长 2026-09-16 审核时点名加的:
    // usual_position 只有四档,边后卫和中卫混在「后卫」里,而边后卫恰恰是
    // 传中/创造机会/xA 的主力,砍掉这张图会让边后卫最有价值的一面消失。
    positions: [1, 2, 3],
  },
  {
    id: "player-defensive-contribution",
    tab: "防守贡献",
    title: "每90分钟防守动作 × 对抗成功率",
    x: PLAYER_METRICS.defensiveActionsPer90,
    y: PLAYER_METRICS.duelWinRate,
    quadrants: ["拦抢俱佳", "以少胜多", "隐身防守", "疲于奔命"],
    note: "横轴是每 90 分钟的抢断+拦截+解围+封堵+回收球——这是国际通行的防守参与度算法,不是本站发明的代理指标。纵轴是全部对抗(地面+空中)里赢下的比例。两轴都是可比优劣的表现指标,但数字高不直接等于「防守好」,也要结合位置与球队整体防守策略一起看。",
    // 后卫/中场(计划表「主场景位置」)。前锋不开放:前场反抢确实有价值,
    // 但解围/封堵这类 CBIRT 主力项在前锋身上几乎为零,同一套口径放进前锋
    // 池里排出来的是"谁回防最多",不是"谁逼抢最好"。
    positions: [1, 2],
  },
  {
    id: "player-progression",
    tab: "持球推进",
    title: "每90分钟触球数 × 每百次触球送进前场传球数",
    x: PLAYER_METRICS.touchesPer90,
    y: PLAYER_METRICS.progressionRate,
    quadrants: ["推进核心", "简洁高效", "推进乏力", "原地打转"],
    note: "横轴是每 90 分钟触球次数,纵轴是每百次触球里有多少次传球送进了前场——两轴都是打法特征,不是强弱评价。触球数同时是纵轴的分母,触球越多,「送进前场」这个比例天然会被拉低,读图时留意这层影响。",
    // 中场/后卫(计划表「主场景位置」)。前锋不开放:前锋本来就站在前场,
    // "把球送进前场"对他们不构成一个有意义的动作,纵轴口径直接失真。
    positions: [1, 2],
  },
  {
    id: "player-goalkeeping",
    tab: "门将",
    title: "每90分钟面对射正预期进球 × 扑救超额",
    x: PLAYER_METRICS.xgotFacedPer90,
    y: PLAYER_METRICS.goalsPreventedPer90,
    // 2026-09-16 真实事故修正:quadrantOf 的四象限顺序是
    // [x好y好, x差y好, x差y差, x好y差](见 quadrantViews.ts::quadrantOf),
    // 之前这里第 2/4 项手误写反——把"高压力但产出好"和"低压力但产出一般"
    // 两句话摆错了位置,导致这两种真实情况的门将在图上被贴反标签。用
    // frontend/tests/quadrant-label-index.test.ts 的通用核查兜底,不再靠
    // 人工数数。
    quadrants: ["低压力且高产出", "高压力下站得住", "高压力且吃紧", "低压力但产出一般"],
    note: "横轴是每 90 分钟面对的射正预期进球(承压程度,已反转,越靠左说明球队防线把对手逼到的射门位置越差),纵轴是扑救超额(面对的预期进球减去实际失球)——纵轴是短期窗口的结果记录,不是稳定的门将能力评价,不代表未来表现。",
    positions: [0],
  },
  {
    id: "player-goalkeeper-distribution",
    tab: "门将出球",
    title: "传球成功率 × 长传占比",
    x: PLAYER_METRICS.passCompletionRate,
    y: PLAYER_METRICS.longBallShare,
    // 顺序按 quadrantOf 的真实约定 [x好y好, x差y好, x差y差, x好y差]
    // (见 player-goalkeeping 的同一处修正注释,这里是同一次排查一并核实的)。
    quadrants: ["全能型门将", "传统型门将", "保守型门将", "清道夫型门将"],
    note: "横轴是传球成功率,纵轴是长传占全部传球的比例——两轴共用同一个分母(传球尝试总数),不会出现两种口径打架。这是打法特征(短传出球型 vs 长传解围型),不是强弱评价。四个象限对应四类门将出球风格:「全能型门将」短传精准、也常开大脚,会视场上局势灵活切换出球方式;「传统型门将」擅长开大脚解围,脚下短传处理一般,常见于打法更直接的球队体系,不代表技术差;「保守型门将」出球两头都不突出,可能只是出球本就不是这名门将的核心武器(比如更依赖反应扑救);「清道夫型门将」(sweeper-keeper)短传精准但极少开大脚,更多用脚下技术直接参与球队短传体系,是现代传控体系青睐的门将类型。仅 2026/2027 起的赛季有数据:传球尝试总数这个字段更早的赛季历史上几乎不下发。",
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

/** 每象限画几个人。2026-09-16 从 10 降到 5(站长:"这图里不需要这么多人,
 *  我可能只需要看最极端的几个人,绝大多数人肯定是都在中间的")。
 *
 *  10 的时候一张图实测画出 35–40 个头像,英超中场「射门与终结」的"锋线哑火"
 *  象限 36 个达标球员里挑 10 个,挑出来的还都是最贴角落的那批,叠成一团
 *  根本分不出谁是谁。降到 5 之后每张图稳定 20 个——这也正是
 *  crestQuadrantLayout 的尺寸参数当初的设计点位(球队象限图 20 支球队),
 *  40 个点是后来硬塞进去的。 */
export const PLAYERS_PER_QUADRANT = 5;

/** "离均值太近就别画了"的门槛,单位是**标准差**(两轴归一化后的欧氏距离)。
 *
 *  站长的原话是"绝大多数人肯定是都在中间的"。实测下来这条其实是**安全网**
 *  而不是常规过滤器:取了每象限前 5 之后,英超外场三张图里最不极端的那个
 *  也在 0.79–1.52 个标准差之外,阈值一个人都不会掉;只有门将那张(达标池小、
 *  挤在一起)会掉 5 个真正贴着均值的人。
 *
 *  换句话说它只在"某个象限稀疏到前 5 名其实就是平均水平"时才生效——那种
 *  情况下与其凑满名额,不如老实少画几个,别把平庸的人包装成某一类的代表。 */
export const MIN_EXTREMENESS = 0.6;

/**
 * 每个象限只画离均值最远的 N 人,且离均值太近的一律不画。
 *
 * 均值(mx/my)必须由调用方传入**全部达标球员**算出的均值(不是只由画出来的
 * 这些人算),这条不变量由 playerPlotSet → topPerQuadrant 的调用顺序保证:
 * 先用 plotSet 的全部 pts 算 mx/my,再用 topPerQuadrant 从这批 pts 里挑。
 *
 * 复用 outlierNames 同一套"两轴标准差归一化后按离均值距离排序"算法,
 * 只是这里按象限分组后各自独立排序、各取前 N,不是全局取一批。
 *
 * `nearMeanDropped` 是"进了前 N 名额、但因为离均值太近而没画"的人数,
 * 供 quadrantTruncationNote 如实披露(CLAUDE.md 禁止静默截断)。
 */
export function topPerQuadrant(
  pts: PlayerPt[],
  mx: number,
  my: number,
  dirs: Dirs,
  take = PLAYERS_PER_QUADRANT,
  minExtremeness = MIN_EXTREMENESS,
): { drawn: PlayerPt[]; totalByQuadrant: number[]; nearMeanDropped: number } {
  const byQuadrant: PlayerPt[][] = [[], [], [], []];
  for (const p of pts) byQuadrant[quadrantOf(p, mx, my, dirs)].push(p);

  const sd = (vals: number[], m: number) =>
    Math.sqrt(vals.reduce((a, v) => a + (v - m) ** 2, 0) / (vals.length || 1)) || 1;
  const sx = sd(pts.map((p) => p.x), mx);
  const sy = sd(pts.map((p) => p.y), my);
  const dist2 = (p: PlayerPt) => ((p.x - mx) / sx) ** 2 + ((p.y - my) / sy) ** 2;
  // 距离阈值在平方空间比较,省掉每个点开一次根号
  const floor2 = minExtremeness * minExtremeness;

  const drawn: PlayerPt[] = [];
  const totalByQuadrant = byQuadrant.map((group) => group.length);
  let nearMeanDropped = 0;
  for (const group of byQuadrant) {
    const sorted = [...group].sort((a, b) => dist2(b) - dist2(a));
    for (const p of sorted.slice(0, take)) {
      if (dist2(p) >= floor2) drawn.push(p);
      else nearMeanDropped += 1;
    }
  }
  return { drawn, totalByQuadrant, nearMeanDropped };
}

/** 没画出来的两种原因要分开说(与 playerHiddenNote 的"样本不达标"又是第三种,
 *  三者文案不能混为一谈,CLAUDE.md 禁止静默截断):
 *  ① 象限内人太多,只挑了最极端的 N 个;
 *  ② 名额没满,但那几个人离均值太近,宁可不画。 */
export function quadrantTruncationNote(
  totalByQuadrant: number[],
  take = PLAYERS_PER_QUADRANT,
  nearMeanDropped = 0,
): string {
  const truncated = totalByQuadrant.some((n) => n > take);
  const out: string[] = [];
  if (truncated) out.push(`每象限只画离均值最远的 ${take} 人`);
  if (nearMeanDropped > 0) {
    out.push(`另有 ${nearMeanDropped} 人离联赛平均太近、算不上任何一类,也没画`);
  }
  if (!out.length) return "";
  return `${out.join("；")}；虚线均值统计全部达标球员（不受这两条影响）。`;
}
