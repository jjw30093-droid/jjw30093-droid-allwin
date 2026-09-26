/**
 * 联赛球队象限图的视角定义与纯逻辑(2026-09-09 从 TeamQuadrantChart.tsx 抽出,
 * 避免 "use client" 组件与 option 构造器循环引用;组件只剩接线)。
 *
 * 三个视角:
 *   攻防 —— 场均预期进球 xG × 场均预期失球 xGA(y 轴反转:越靠上防守越好)
 *   战术 —— 运动战 xG × 定位球 xG(全站独一份的战术叙事素材)
 *   射门质量 —— 场均射门数 × 场均预期进球(量 vs 质:广种薄收 / 少而精)
 *
 * 文案纪律(2026-09-09 术语校准):象限名一律用中文足球术语("对攻型"
 * "广种薄收"),不用篮球词("出手")、不用口语("两条路都通");
 * 2026-09-26 例外(站长明确要求大白话):攻防视角的象限名改为"两头都强/
 * 守强攻弱/两头都弱/对攻型";
 * 射门多而 xG 低通常是远射多,但没有射门距离数据,所以用"广种薄收"描述现象,
 * 不写"远射为主"去断言原因。
 *
 * 诚实纪律:
 * - 缺数据的球队直接不画,不补 0(0 在 xG 语境里是"一次机会都没创造",
 *   是有意义的真实值,不能拿来当缺失占位)。
 * - 攻防视角依赖 fact_league_table 的 xg 档,并非每个联赛赛季都有;
 *   数据不足时该视角禁用并写明原因,不静默回退。
 * - 参考线是**当前筛选窗口内**(未筛选时即本联赛本赛季)的平均值,不是跨联赛
 *   基准 —— 摘要里说清楚(2026-09-14"最近 N 场/主客场"筛选新增后,虚线随
 *   筛选状态变化,不再恒等于整赛季平均)。
 */

import type { TeamSeasonStatRow } from "@/lib/api-v1";
import { METRICS, meetsSample, sampleText, teamKey, type MetricDef } from "./teamMetrics";

export { teamKey };

/** 轴就是指标(2026-09-14 改造):Axis = MetricDef,只多一个可选的轴上短名
 *  (图表 nameGap 只有 26px,详情面板/摘要仍用 label,e2e 逐字依赖那串文案)。 */
export type Axis = MetricDef & {
  axisName?: string;
};
export const axisLabel = (a: { axisName?: string; label: string }) => a.axisName ?? a.label;

/** 轴标题与方向提示的可选文案(2026-09-26 象限图"读得懂"改造)。
 *  plain:大白话主标签,原指标 label(如"场均预期进球 xG")退为副标签;
 *  role:这根轴在攻防叙事里的角色("进攻"/"防守"),只加在方向提示前面。
 *  只影响图上的轴标题与端点提示——tooltip/详情面板/摘要仍读 label,e2e 逐字依赖。 */
export type AxisCopy = { plain?: string; role?: string };

export function axisTitle(
  a: { axisName?: string; label: string },
  copy?: AxisCopy,
): { main: string; sub?: string } {
  return copy?.plain ? { main: copy.plain, sub: a.label } : { main: axisLabel(a) };
}

/**
 * 轴端方向提示:告诉读者"往哪边读是好"。
 * - 只有 performance 轴才写"越好"——style(打法特征)与 outcome_variance
 *   (短期结果记录)不代表强弱/能力,只能写"数值越大"。
 * - y 轴反转(lowerIsBetter)后"好"在上方,所以 y 轴的"越好"永远朝上;
 *   x 轴 lowerIsBetter 时"好"在左方。
 */
export function axisHint(
  a: { lowerIsBetter?: boolean; semantic?: "performance" | "style" | "outcome_variance" },
  which: "x" | "y",
  copy?: AxisCopy,
): string {
  const role = copy?.role ? `${copy.role} ` : "";
  const performance = a.semantic === "performance";
  const lower = a.lowerIsBetter === true;
  if (which === "x") {
    if (performance) return lower ? `${role}← 越往左越好` : `${role}→ 越往右越好`;
    return lower ? `${role}← 越往左数值越小` : `${role}→ 越往右数值越大`;
  }
  // y 轴:lowerIsBetter 时轴已反转,上方=数值小=好
  if (performance) return `${role}↑ 越往上越好`;
  return lower ? `${role}↑ 越往上数值越小` : `${role}↑ 越往上数值越大`;
}

export type ViewGroupId = "overview" | "attack" | "quality" | "control" | "defence";

export type ViewGroup = {
  id: ViewGroupId;
  label: string;
  /** 选中该类别时显示的一行说明(CLAUDE.md §11.2 的文字摘要义务) */
  blurb: string;
};

export const VIEW_GROUPS: ViewGroup[] = [
  { id: "overview", label: "攻防总览", blurb: "整体创造与让出的对比。" },
  { id: "attack", label: "进攻构成", blurb: "进球机会从哪来。" },
  { id: "quality", label: "射门质量", blurb: "射得多不等于射得好。" },
  { id: "control", label: "控球与推进", blurb: "球权在谁脚下，有没有真的推进到前场。" },
  { id: "defence", label: "防守承压", blurb: "对手在本队门前拿到了什么。" },
];

export type View = {
  id: string;
  group: ViewGroupId;
  tab: string;
  title: string;
  x: Axis;
  y: Axis;
  /** 四象限中文名,顺序按**好/差**:x好y好 / x差y好 / x差y差 / x好y差
   *  (不是高/低 —— "场均预期失球"越低越好,见 quadrantOf)。x 轴反向
   *  (x.lowerIsBetter)时"x好"在**左**半边,这类视角的 note 必须写明
   *  x 轴从右往左读。 */
  quadrants: [string, string, string, string];
  /** 图上方的一句话说明(2026-09-26:原先只有一大段 note,收进"?"提示;
   *  一句话放在图上方让人扫一眼就知道这张图在比什么)。 */
  summary: string;
  /** 完整口径说明,收在"?"提示里 */
  note: string;
  /** 图上轴标题/方向提示的可选文案,见 AxisCopy */
  axisCopy?: { x?: AxisCopy; y?: AxisCopy };
  /** 详情面板里跟这个视角相关的补充指标(各自带联赛排名),不堆全部 17 项 */
  related: MetricDef[];
};

/** 按 id 取视角,找不到直接抛错(不允许静默回退到 undefined)。测试与调用方
 *  优先用它而不是 VIEWS[下标]——分组/重排后下标会静默换掉被覆盖的对象。 */
export function viewById(id: string): View {
  const v = VIEWS.find((x) => x.id === id);
  if (!v) throw new Error(`未知视角 id: ${id}`);
  return v;
}

/** 按 VIEWS 原始顺序分组;空组(还没有视角的类别)直接不出现。 */
export function groupedViews(views: View[] = VIEWS): { group: ViewGroup; views: View[] }[] {
  return VIEW_GROUPS.map((group) => ({ group, views: views.filter((v) => v.group === group.id) })).filter(
    (g) => g.views.length > 0,
  );
}

export const VIEWS: View[] = [
  {
    id: "both-ends",
    group: "overview",
    tab: "攻防",
    title: "预期进球 × 预期失球",
    x: METRICS.xg,
    y: METRICS.xga,
    // 2026-09-26 站长要求大白话(推翻 2026-09-09 的术语命名):象限名按
    // 好/差顺序 = x好y好 / x差y好 / x差y差 / x好y差。
    quadrants: ["两头都强", "守强攻弱", "两头都弱", "对攻型"],
    summary: "每队场均创造多少机会、又让对手创造了多少机会。",
    axisCopy: { x: { plain: "场均创造的机会", role: "进攻" }, y: { plain: "场均被对手创造的机会", role: "防守" } },
    note: "横轴是本队每场制造出多少质量的射门机会（预期进球），纵轴是对手在本队门前每场拿到多少（预期失球）。预期进球/预期失球衡量的是射门机会的质量，不是实际进球数。",
    related: [METRICS.shotsOnTarget, METRICS.cleanSheets, METRICS.bttsPct],
  },
  {
    id: "tactics",
    // 与 both-ends 同组(overview):两者都是"整体看这支球队攻防长什么样"的
    // 总览视角,放同一组也让默认分组在任一视角被禁用时,仍同时展示"缺数据
    // 的那个 tab(带说明)"和"实际回退选中的 tab"——不必额外点一次分类
    // 才能看到禁用原因。射门质量(volume)同理留在这组。等这一组视角变多、
    // 真的需要拆分时再分组,不为了"看起来像分了类"而提前拆。
    group: "overview",
    tab: "战术",
    title: "运动战 × 定位球",
    x: METRICS.openPlayXg,
    y: METRICS.setPlayXg,
    quadrants: ["多点开花", "依赖定位球", "进攻乏术", "运动战主导"],
    summary: "进球机会主要来自运动战，还是定位球。",
    note: "运动战 xG 来自流畅进攻，定位球 xG 来自角球/任意球/界外球后的机会。两项相加约等于非点球 xG，剩下的是点球。",
    related: [METRICS.nonPenXg, METRICS.penXg, METRICS.corners],
  },
  {
    id: "volume",
    group: "overview", // 同上 tactics 的分组说明
    tab: "射门质量",
    title: "射门数量 × 机会质量",
    x: METRICS.totalShots,
    y: METRICS.xg,
    quadrants: ["量质齐优", "少而精", "量质皆低", "广种薄收"],
    summary: "射门多不多，每次射门的机会质量高不高。",
    note: "同样的射门数，预期进球越高说明射门位置越好。右下角是打得多但位置差，左上角是射门少但每次都在好位置。",
    related: [METRICS.shotsOnTarget, METRICS.xgot, METRICS.xgPerShot],
  },
  {
    id: "possession-passing",
    group: "control",
    tab: "推进方式",
    title: "前场传球占比",
    x: METRICS.oppHalfPassShare,
    y: METRICS.totalShots,
    quadrants: ["前场压制", "少传多射", "推进乏力", "倒脚少射"],
    summary: "传球有多少发生在对方半场，配上场均射门数。",
    note: "横轴是成功传球里有多大比例发生在对方半场，纵轴是场均射门数——这是本站用现有数据算的代理指标，不是 Opta/StatsBomb 的官方 Field Tilt。",
    related: [METRICS.shotsOnTarget, METRICS.xg],
  },
  {
    id: "set-piece-both-ends",
    group: "overview",
    tab: "定位球攻防",
    title: "定位球攻防",
    x: METRICS.setPieceXgShare,
    y: METRICS.setPieceXgaShare,
    // 两根轴都是 style(打法特征,不代表强弱),四象限一律用中性打法原型
    // 命名,不用好/差措辞——右上角"定位球拉锯"不代表比左下角"运动战对决"更强。
    quadrants: ["定位球拉锯", "防空吃紧", "运动战对决", "定位球见长"],
    summary: "进攻和防守两端，各自有多依赖定位球。",
    note: "横轴是本队进攻端定位球 xG 占运动战+定位球 xG 的比例，纵轴是对手打进来的威胁里定位球占的比例（同一套口径，取自对手）。两轴都是打法特征，不是强弱评价。",
    related: [METRICS.corners, METRICS.setPlayXg],
  },
  // 2026-09-14 站长审核发现:「定位球依赖」(x=定位球xG占比, y=场均运动战xG
  // 绝对值)与既有「战术」视角(x=运动战xG绝对值, y=定位球xG绝对值)高度重叠
  // ——战术视角本来就有一个象限直接叫"依赖定位球",讲的是同一对原始数字
  // (运动战 xG、定位球 xG),只是换了呈现口径(绝对值 vs 占比)。三个"定位球
  // 相关"视角(战术/定位球攻防/定位球依赖)信息重叠过多,「定位球攻防」是
  // 唯一同时覆盖进攻端与防守端的(战术视角完全没有防守信息),保留它、
  // 删掉这一个。METRICS.setPieceXgShare/openPlayXg 仍被其它视角使用,不删。

  // ── 包三 ──────────────────────────────────────────────────────────
  {
    id: "attack-defence-quality",
    group: "overview",
    tab: "攻防质量",
    title: "攻防质量",
    x: METRICS.xgPerShot,
    y: METRICS.oppXgPerShot,
    quadrants: ["攻守俱精", "守稳攻钝", "攻钝守险", "大开大合"],
    summary: "每脚射门的成色：自己射得好不好，对手射得好不好。",
    note: "横轴是我方每脚射门平均创造多少预期进球（射门成色），纵轴是对手每脚射门平均能换来多少（越靠上说明防线把对手逼到的射门位置越差）。这是既有「预期进球 × 预期失球」视角的成色版——两队场均 xG 相同，一队可能是好机会打出来的，另一队可能是数量堆出来的，这张图能分开。",
    related: [METRICS.totalShots, METRICS.xg, METRICS.xga],
  },
  {
    id: "shot-quality-accuracy",
    group: "quality",
    tab: "质量与准星",
    title: "质量与准星",
    x: METRICS.xgPerShot,
    y: METRICS.shotAccuracy,
    quadrants: ["有质有准", "准星尚在", "攻门粗糙", "屡失良机"],
    summary: "每脚射门的平均质量，配上射正率。",
    note: "横轴是每脚射门的平均质量，纵轴是射正率（已排除被封堵射门）。右下角「屡失良机」是位置好但打飞/打偏多的球队。",
    related: [METRICS.shotsOnTarget, METRICS.totalShots],
  },
  {
    id: "box-shots",
    group: "attack",
    tab: "禁区内外",
    title: "禁区内外",
    x: METRICS.boxShotShare,
    y: METRICS.totalShots,
    quadrants: ["围攻禁区", "远射成风", "攻势零散", "禁区精准"],
    summary: "射门有多少发生在禁区内，配上场均射门数。",
    note: "横轴是射门里有多大比例在禁区内完成，纵轴是场均射门数。同样是射门多，位置好坏差很多。",
    related: [METRICS.xgPerShot, METRICS.shotsOnTarget],
  },
  {
    id: "fast-break-possession",
    group: "attack",
    tab: "反击与控球",
    title: "反击与控球",
    x: METRICS.fastBreakXgShare,
    y: METRICS.possession,
    quadrants: ["控反兼备", "阵地推进", "低位固守", "防守反击"],
    summary: "进攻威胁有多少来自快速反击，配上控球率看打法。",
    note: "横轴是进攻威胁里有多大比例来自快速反击（取自射门级数据，分母是全部非点球 xG），纵轴是控球率。右下角「防守反击」是控球率低、但机会多靠转换创造的球队。",
    related: [METRICS.xg, METRICS.totalShots],
  },
  {
    id: "chance-conversion",
    group: "quality",
    tab: "机会转化",
    title: "机会转化",
    x: METRICS.xgPerShot,
    y: METRICS.bigChanceConversion,
    quadrants: ["机会尽收", "关键制胜", "创造匮乏", "浪费良机"],
    summary: "每脚射门的平均质量，配上绝佳机会的把握率。",
    note: "横轴是每脚射门的平均质量，纵轴是绝佳机会把握率（大机会里有多大比例真的打进）。右下角「浪费良机」是机会质量不差、但绝佳机会经常糟蹋的球队——样本小时这个数会被一两次运气波动带偏，请配合样本量一起看。",
    related: [METRICS.shotsOnTarget, METRICS.xg],
  },
  {
    id: "aerial-physicality",
    group: "defence",
    tab: "空中对抗",
    title: "空中对抗",
    x: METRICS.aerialWinShare,
    y: METRICS.fouls,
    // 两轴都是 style(打法/身体对抗特征),不代表强弱。
    quadrants: ["强悍对抗", "犯规频繁", "回避对抗", "制空占优"],
    summary: "争顶优势占比，配上场均犯规数。",
    note: "横轴是全场争顶里本队赢下的比例（没有单独的“争顶总数”字段，用双方赢下次数之和近似分母），纵轴是场均犯规。两轴都是身体对抗风格的描述，不是强弱评价。",
    related: [METRICS.corners],
  },
  {
    id: "box-pressure",
    group: "control",
    tab: "禁区压制",
    title: "禁区压制",
    x: METRICS.boxTouchShare,
    y: METRICS.npxgPerBoxTouch,
    // ⚠ 轻度机械相关:触球数同时在 x 的分子与 y 的分母。
    quadrants: ["压制转化", "一击致命", "难入禁区", "禁区空转"],
    summary: "对方禁区触球的份额，配上每次触球换来的机会质量。",
    note: "横轴是双方对方禁区触球里本队占的份额，纵轴是每次禁区触球平均换来多少非点球 xG——这是本站目前最接近 Field Tilt 的字段组合，但不是官方 Field Tilt。仅 2024 年起的赛季有数据（更早的赛季数据源随机缺失，球队间不可比，该视角会因样本不足自动禁用）。",
    related: [METRICS.xgPerShot],
  },
  {
    id: "territory-pressing",
    group: "defence",
    tab: "阵地与拼抢",
    title: "阵地与拼抢",
    x: METRICS.oppTerritoryShare,
    y: METRICS.defActionDensity,
    // ⚠ 中度机械相关:被压通常伴随对手控球高，而 y 的分母是对手传球数。
    quadrants: ["低位缠斗", "前场紧逼", "控球压制", "收缩退守"],
    summary: "对手把球玩到我方半场的比例，配上防守动作密度。",
    note: "横轴是对手把球玩到我方半场的比例（阵地证据），纵轴是防守动作密度——这是基于阵地与动作密度的近似判断，不是 PPDA。单看防守动作密度分不出高位压迫和铁桶阵，加上「对手根本进不了我们半场」这条阵地证据之后才敢用「前场紧逼」这个词。",
    related: [METRICS.fouls],
  },
  {
    id: "corner-quality",
    group: "attack",
    tab: "角球成色",
    title: "角球成色",
    x: METRICS.cornerShotRate,
    y: METRICS.corners,
    quadrants: ["角球利器", "角球空转", "角球平平", "角球精准"],
    summary: "角球有多少变成射门，配上场均角球数。",
    note: "横轴是角球真正形成射门的比例（分母通常较小，详情面板按原始计数展示，不只给百分比），纵轴是场均角球数。",
    related: [],
  },
  {
    id: "finishing-record",
    group: "quality",
    tab: "终结记录",
    title: "终结记录",
    x: METRICS.nonPenaltyShotsPerMatch,
    y: METRICS.finishingDelta,
    quadrants: ["火力全开", "效率惊人", "手感冰凉", "雷声大雨点小"],
    summary: "场均非点球射门数，配上比预期多进（少进）了多少球。",
    note: "横轴是场均非点球射门数，纵轴是场均（非点球进球 − 非点球预期进球）——这是短期窗口的结果记录，不是稳定的终结能力，调研认为这类差值跨赛季的相关性接近零。",
    related: [],
  },
  {
    id: "defence-goalkeeping",
    group: "defence",
    tab: "防线与门将",
    title: "防线与门将",
    x: METRICS.oppXgPerShot,
    y: METRICS.gkSavesAboveExpected,
    quadrants: ["门线双稳", "门将救主", "门户洞开", "防线独撑"],
    summary: "防线让对手的射门有多差，配上门将比预期多扑出多少。",
    note: "横轴是对手每脚射门 xG，反转过来读——越靠左（数值越低）说明防线把对手逼到了更差的射门位置，越低越好。纵轴是场均门将扑救超额——这是短期窗口的近期记录，不代表未来表现，且被扑救射门的 xGOT 约三分之一缺失，图下详情面板会公示实际纳入统计的有效射正次数。xG 是射门前的位置质量，xGOT 是射门后、只算射正的落点质量，两者是不同信号，不是同一个数字换了个名字。",
    related: [],
  },
];

export type Pt = {
  key: string;
  name: string;
  x: number;
  y: number;
  mp: number | null;
  /** 队徽 URL,后端确无本地已验证 PNG 时为 null —— 此时降级为象限色圆点,
   *  并优先想显示队名(不能让这支球队从图上消失);但若队名会压住旁边的
   *  队徽,quadrantOption.ts 的 resolveLabelVisibility 仍会摘掉这个标签——
   *  该队的圆点、点击、下方分组名单都不受影响,只是不再飘字。 */
  crestUrl: string | null;
  teamId: number | null;
};

/** plotSet/collectPoints 只需要两根轴的取值函数与门槛;View 结构上满足它,
 *  测试可以直接传 METRICS 里的指标对象(比手写列名更贴近真实代码路径)。 */
export type __TestView = {
  x: Pick<Axis, "value" | "sample">;
  y: Pick<Axis, "value" | "sample">;
};

export type HiddenTeam = {
  key: string;
  name: string;
  axis: "x" | "y";
  /** missing = 数据源这一项根本没给;sample = 有值,但样本不够,不画 */
  reason: "missing" | "sample";
};

export type PlotSet = { pts: Pt[]; hidden: HiddenTeam[] };

/** `windowScale`(2026-09-14"最近 N 场/主客场"筛选新增,默认 1 = 不缩放)
 *  按当前筛选窗口把每根轴的样本门槛等比缩小,见 teamMetrics.ts::windowScaleFor。 */
export function plotSet(rows: TeamSeasonStatRow[], view: __TestView, windowScale = 1): PlotSet {
  const pts: Pt[] = [];
  const plotted = new Set<string>();
  // Map 而不是数组:同一支球队出现多行时不能被记两次"未画出";后面的行
  // 补上了数据就要把前面的记录撤掉(保持既有"后行可救"语义)。
  const hiddenByKey = new Map<string, HiddenTeam>();
  for (const r of rows) {
    const key = teamKey(r.team);
    if (plotted.has(key)) continue; // 已画上的队,后续重复行一律忽略
    const name = r.team.name;
    const x = view.x.value(r);
    const y = view.y.value(r);
    // 缺一个维度就整点丢弃 —— 半个坐标画不出散点,补 0 会造出假的"极端球队"
    if (x == null || y == null) {
      hiddenByKey.set(key, { key, name, axis: x == null ? "x" : "y", reason: "missing" });
      continue;
    }
    // 样本不足的不画,但必须能被数出来:图下方要说清楚藏了几支、为什么
    const badAxis = !meetsSample(r, view.x, windowScale)
      ? "x"
      : !meetsSample(r, view.y, windowScale)
        ? "y"
        : null;
    if (badAxis) {
      hiddenByKey.set(key, { key, name, axis: badAxis, reason: "sample" });
      continue;
    }
    plotted.add(key);
    hiddenByKey.delete(key);
    pts.push({
      key,
      name,
      x,
      y,
      mp: r.matches_played ?? null,
      crestUrl: r.team.crest_url ?? null,
      teamId: r.team.team_id ?? null,
    });
  }
  return { pts, hidden: [...hiddenByKey.values()] };
}

/** 旧签名保留:调用方只要点集时用这个(组件、option 构造器、既有测试)。 */
export const collectPoints = (rows: TeamSeasonStatRow[], view: __TestView, windowScale = 1): Pt[] =>
  plotSet(rows, view, windowScale).pts;

/** 藏了几支球队、为什么——一句话,图下脚注与 ariaSummary 共用同一个产物,
 *  两处不可能再飘走。没有隐藏时返回空串,此时摘要与改造前逐字一致(e2e 依赖)。
 *  `windowScale` 同 plotSet,门槛说明文案要与实际生效的门槛一致。 */
export function hiddenNote(hidden: HiddenTeam[], view: { x: Axis; y: Axis }, windowScale = 1): string {
  const names = (hs: HiddenTeam[]) => hs.map((h) => h.name).join("、");
  const bySample = hidden.filter((h) => h.reason === "sample");
  const byMissing = hidden.filter((h) => h.reason === "missing");
  const out: string[] = [];
  if (bySample.length) {
    const rules = [...new Set([view.x, view.y].map((a) => sampleText(a, windowScale)).filter(Boolean))].join("；");
    out.push(`另有 ${bySample.length} 支球队样本不足未画出${rules ? `(门槛:${rules})` : ""}：${names(bySample)}`);
  }
  if (byMissing.length) {
    out.push(`${byMissing.length} 支球队数据源缺这两项之一，同样未画出：${names(byMissing)}`);
  }
  if (!out.length) return "";
  return out.join("；") + "。虚线平均值只统计画出的球队。";
}

/** 临界带:均值线两侧各 5%(2026-09-26)。落在带内的球队,归属哪个象限
 *  很容易随几场比赛翻转——下方名单里标"临界",图上画淡色带。 */
export const NEAR_MEAN_FRAC = 0.05;

/** 带的半宽(数据单位)。默认取均值绝对值的 5%;轴上有负值(如终结超额、
 *  扑救超额,均值贴近 0)时按均值算会退化成几乎没有带,改用数据跨度的 5%。 */
export function meanBandHalf(m: number, values: number[]): number {
  if (!values.length) return 0;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  return lo < 0 ? NEAR_MEAN_FRAC * (hi - lo) : NEAR_MEAN_FRAC * Math.abs(m);
}

/** 任一轴落在均值线临界带内的球队 key(含边界)。 */
export function nearMeanTeams(
  pts: { key: string; x: number; y: number }[],
  mx: number,
  my: number,
  bandX: number,
  bandY: number,
): Set<string> {
  return new Set(
    pts.filter((p) => Math.abs(p.x - mx) <= bandX || Math.abs(p.y - my) <= bandY).map((p) => p.key),
  );
}

export const mean = (nums: number[]) => nums.reduce((a, b) => a + b, 0) / nums.length;

/** "虚线是……平均值"这句话里,……部分随筛选状态变化(2026-09-14"最近 N 场/
 *  主客场"筛选新增)——不筛选时是"本联赛本赛季",筛了就换成"最近 5 场"/
 *  "主场"/"最近 5 场主场"这类更精确的描述,不能继续说"本赛季平均"却其实
 *  只统计了最近几场。 */
export function filterWindowLabel(recency: number | null | undefined, venue: string | undefined): string {
  const venueLabel = venue === "home" ? "主场" : venue === "away" ? "客场" : "";
  if (recency != null && venueLabel) return `最近${recency}场${venueLabel}`;
  if (recency != null) return `最近${recency}场`;
  if (venueLabel) return venueLabel;
  return "本联赛本赛季";
}

/** 两根轴各自的"越小越好"。传 boolean 是旧调用方的兼容形态,只表示 y。 */
export type Dirs = { x?: boolean; y?: boolean };

/**
 * 点落在哪个象限,索引按**好/差**而不是高/低:
 *   0 = x 好 y 好, 1 = x 差 y 好, 2 = x 差 y 差, 3 = x 好 y 差
 *
 * 平局规则(有意不对称,两根轴同一条规则):越大越好的轴,恰好等于均值算
 * **好**侧(>=);越小越好的轴,恰好等于均值算**差**侧(严格 <)。这条不对称
 * 是刻意的:一支恰好压在均值上的球队只会被算作一侧,不会因为两根轴各自
 * "包含等号"被塞进最好的那个象限。
 *
 * y 轴必须传 lowerIsBetter —— "场均预期失球"越小越好,若按数值高低命名,
 * 真正攻守兼备的球队会被贴上"对攻型",和配色正好互相打架。x 轴同理支持。
 */
export function quadrantOf<P extends { x: number; y: number }>(
  p: P,
  mx: number,
  my: number,
  lowerIsBetter: boolean | Dirs = false,
): number {
  const d: Dirs = typeof lowerIsBetter === "boolean" ? { y: lowerIsBetter } : lowerIsBetter;
  const xGood = d.x ? p.x < mx : p.x >= mx;
  const yGood = d.y ? p.y < my : p.y >= my;
  if (xGood && yGood) return 0;
  if (!xGood && yGood) return 1;
  if (!xGood && !yGood) return 2;
  return 3;
}

/** 2026-09-15 起参数放宽成结构类型(不要求完整 Axis/MetricDef 形状)——
 *  球队象限图与球员象限图各自有一套 MetricDef(value 的行参类型不同,
 *  TeamSeasonStatRow vs PlayerQuadrantRow,严格模式下互不兼容),但两边的
 *  Axis 在 label/unit/digits/lowerIsBetter 这几个字段上形状完全一致——
 *  fmt/dirsOf/axisLabel 只读这几个字段,没必要绑定某一套具体的 MetricDef。
 *  quadrantOption.ts(唯一的图表 option 构造器)据此对两边通用,不必复制。 */
export type AxisLike = { label: string; unit: string; digits: number; lowerIsBetter?: boolean };

export function fmt(v: number, a: AxisLike) {
  return `${v.toFixed(a.digits)}${a.unit}`;
}

/** 三个调用点(组件 / option / 详情面板)统一从 view 取方向,不再各自记 lowY。 */
export const dirsOf = (v: { x: AxisLike; y: AxisLike }): Dirs => ({
  x: v.x.lowerIsBetter === true,
  y: v.y.lowerIsBetter === true,
});

/** 2026-09-14 起 Axis 就是 MetricDef(多了可选的 axisName),这里是恒等转换。
 *  不删:TeamQuadrantDetail 与既有测试都在调用,留着这层命名也把"轴 = 指标"
 *  这件事写在代码里。 */
export const axisToMetric = (a: Axis): MetricDef => a;

/**
 * 只给"离联赛平均最远"的几支球队标队名。
 *
 * 队徽本身已经是身份标识,文字只留给极值球队(它们才是这张图要讲的东西);
 * 无队徽的球队和被选中的球队由调用方并进集合,优先带名字——但最终是否
 * 真的显示,还要过 quadrantOption.ts::resolveLabelVisibility 的队徽碰撞检测。
 * 距离按各轴标准差归一化,否则量纲大的轴(射门数 ~12)会完全压过小的(xG ~1.4)。
 */
export function outlierNames(
  pts: { name: string; x: number; y: number }[],
  mx: number,
  my: number,
  take = 6,
): Set<string> {
  const sd = (vals: number[], m: number) =>
    Math.sqrt(vals.reduce((a, v) => a + (v - m) ** 2, 0) / vals.length) || 1;
  const sx = sd(pts.map((p) => p.x), mx);
  const sy = sd(pts.map((p) => p.y), my);
  return new Set(
    [...pts]
      .sort(
        (a, b) =>
          ((b.x - mx) / sx) ** 2 + ((b.y - my) / sy) ** 2 -
          (((a.x - mx) / sx) ** 2 + ((a.y - my) / sy) ** 2),
      )
      .slice(0, take)
      .map((p) => p.name),
  );
}

/** 图上自动标注的一支球队(2026-09-26"有结论")。文字全部由数据生成,不写死任何球队。 */
export type Annotation = {
  key: string;
  name: string;
  /** 1 起的序号,图上队名前缀"①②③"与图下列表对应 */
  no: number;
  /** 这支球队为什么被标注,一条一句(合并同一队命中多项) */
  reasons: string[];
};

export const ANNOTATION_BADGES = ["①", "②", "③"] as const;

type AnnotView = {
  x: Axis;
  y: Axis;
  quadrants: [string, string, string, string];
  axisCopy?: { x?: AxisCopy; y?: AxisCopy };
};

/** 各轴标准差归一化后离两条均值线的综合距离最远的点(与 outlierNames 同一口径)。 */
function farthestPoint<P extends { x: number; y: number }>(pts: P[], mx: number, my: number): P {
  const sd = (vals: number[], m: number) =>
    Math.sqrt(vals.reduce((a, v) => a + (v - m) ** 2, 0) / vals.length) || 1;
  const sx = sd(pts.map((p) => p.x), mx);
  const sy = sd(pts.map((p) => p.y), my);
  const dist = (p: P) => ((p.x - mx) / sx) ** 2 + ((p.y - my) / sy) ** 2;
  return pts.reduce((best, p) => (dist(p) > dist(best) ? p : best), pts[0]);
}

/**
 * 自动标注 3 个点:横轴"最好"的队、纵轴"最好"的队、离两条均值线综合距离最远的队。
 * "最好"由轴方向决定:lowerIsBetter 取最小("联赛最低"),否则取最大("联赛最高");
 * 风格/结果记录轴同一规则,只是措辞里的"最高/最低"不代表强弱。同一队命中多项时合并
 * 成一条。并列时取输入顺序靠前的,结果确定。
 */
export function pickAnnotations(
  pts: Pt[],
  view: AnnotView,
  mx: number,
  my: number,
): Annotation[] {
  if (pts.length < 2) return [];
  const dirs = dirsOf(view);
  const byKey = new Map<string, { pt: Pt; reasons: string[] }>();
  const add = (pt: Pt, reason: string) => {
    const e = byKey.get(pt.key) ?? { pt, reasons: [] };
    e.reasons.push(reason);
    byKey.set(pt.key, e);
  };
  for (const which of ["x", "y"] as const) {
    const axis = view[which];
    const lower = axis.lowerIsBetter === true;
    const best = pts.reduce((b, p) => (lower ? (p[which] < b[which] ? p : b) : p[which] > b[which] ? p : b), pts[0]);
    const title = axisTitle(axis, view.axisCopy?.[which]).main;
    add(best, `${title} ${fmt(best[which], axis)}，联赛${lower ? "最低" : "最高"}`);
  }
  const far = farthestPoint(pts, mx, my);
  add(far, `综合离平均线最远，属于「${view.quadrants[quadrantOf(far, mx, my, dirs)]}」`);
  // 展示顺序按 pts 顺序无关,按首次命中顺序(x 最好 → y 最好 → 最远)
  return [...byKey.values()].map((e, i) => ({ key: e.pt.key, name: e.pt.name, no: i + 1, reasons: e.reasons }));
}

/**
 * 标题下的一句结论:纯模板 + 数据。"离平均最远"的队与它所在象限,括号里各象限球队数
 * (合计恒等于画出的球队数)。meanName 是均值线的叫法("联赛平均"/"最近5场平均"),
 * 筛选状态下不能继续说"联赛平均"。
 */
export function buildConclusion(
  pts: Pt[],
  view: { x: Axis; y: Axis; quadrants: [string, string, string, string] },
  mx: number,
  my: number,
  meanName = "联赛平均",
): string {
  if (!pts.length) return "";
  const dirs = dirsOf(view);
  const far = farthestPoint(pts, mx, my);
  const counts = view.quadrants.map((label, i) => `${label} ${pts.filter((p) => quadrantOf(p, mx, my, dirs) === i).length} 队`);
  return `${far.name}综合离${meanName}最远，属于「${view.quadrants[quadrantOf(far, mx, my, dirs)]}」（${counts.join("、")}）。`;
}

/** 样本偏小的判定:画出的球队里最少的已踢场次 < 10(最近 N 场筛选下,场次就是窗口大小)。 */
export const SMALL_SAMPLE_MATCHES = 10;
export function isSmallSample(pts: { mp: number | null }[]): boolean {
  const mps = pts.map((p) => p.mp).filter((m): m is number => m != null);
  return mps.length > 0 && Math.min(...mps) < SMALL_SAMPLE_MATCHES;
}

export const MAX_SELECTED = 2;

/** 点击切换:已选 → 移除;未选且不足上限 → 追加;满员 → FIFO 顶掉最早的。 */
export function toggleSelection(keys: string[], key: string, max = MAX_SELECTED): string[] {
  if (keys.includes(key)) return keys.filter((k) => k !== key);
  const next = [...keys, key];
  return next.length > max ? next.slice(next.length - max) : next;
}

/** 挡陈旧选中(照抄 ShotMapChart.resolveSelectedShot 的派生态做法):只保留
 *  当前视角仍在图上的 key,顺序按选中先后。泛型化(2026-09-15)以复用给
 *  球员象限图的 PlayerPt——只要求 {key: string},不绑定球队专属字段。 */
export function resolveSelectedTeams<P extends { key: string }>(pts: P[], keys: string[]): P[] {
  const out: P[] = [];
  for (const k of keys) {
    const p = pts.find((q) => q.key === k);
    if (p) out.push(p);
  }
  return out;
}

/** ECharts 点击参数 → 选中键。优先 dataIndex(不经过克隆路径),payload 兜底;
 *  点空白返回 null。泛型化(2026-09-15)同 resolveSelectedTeams。 */
export function resolveClickedKey<P extends { key: string }>(params: unknown, pts: P[]): string | null {
  const p = params as {
    seriesName?: string;
    dataIndex?: number;
    data?: { pt?: { key?: string } };
  } | null;
  if (!p) return null;
  if ((p.seriesName === "crest" || p.seriesName === "hit") && typeof p.dataIndex === "number") {
    const hit = pts[p.dataIndex];
    if (hit) return hit.key;
  }
  const fromPayload = p.data?.pt?.key;
  return typeof fromPayload === "string" ? fromPayload : null;
}
