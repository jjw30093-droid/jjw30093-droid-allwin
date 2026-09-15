/**
 * 联赛球员象限图的取数纯逻辑(2026-09-15)。对标 teamMetrics.ts,但刻意不
 * 复用它——球员指标全部来自 PlayerQuadrantRow.ratios(与 TeamSeasonStatRow
 * 是两个不相关的形状,MetricDef.value 的行参类型不兼容),且门槛机制也不同:
 * 球队侧是"每个指标各自的 SampleRule"(minMatches/minVolume),球员侧是
 * **单一的页面级出场占比门槛**(minutes_share >= 40%,站长拍板,随赛季进度
 * 自动缩放——分母本身就是"本队已踢分钟数",不需要再按赛季进度另外折算)。
 *
 * 诚实纪律与 teamMetrics.ts 相同:
 * - 缺值一律返回 null,不补 0;
 * - 均值与排名的分母只数真正有值的球员,不是 rows.length;
 * - 目标球员自身缺值时排名为 null(不是"排最后")。
 */

import type { PlayerQuadrantRow } from "@/lib/api-v1";

export type PlayerMetricDef = {
  id: string;
  label: string;
  unit: string;
  digits: number;
  /** true = 数值越小越好(如"面对射正预期进球"),排名升序 */
  lowerIsBetter?: boolean;
  /** performance = 可比优劣;style = 打法特征,不代表强弱;
   *  outcome_variance = 短期结果记录,不是可持续能力(与 backend/metrics/
   *  registry.py 同一套分类)。 */
  semantic?: "performance" | "style" | "outcome_variance";
  /** 口径一句话,详情面板与视角说明逐字复用。 */
  caliber?: string;
  value: (r: PlayerQuadrantRow) => number | null;
};

/** 出场占比门槛(2026-09-15 站长拍板):必须达到"本队已踢总分钟数"的这个
 *  比例才计入象限图,防止替补刷少量出场就冲进极值象限。分母
 *  (team_minutes)已经是"转会球员取更严格的那支队"口径,这里不再需要
 *  按赛季进度另外折算——40% 是单一可调常量,不随赛季进度变化(早期赛季
 *  分母本身就小,同一个百分比天然适配)。 */
export const MINUTES_SHARE_THRESHOLD = 0.4;

export function meetsMinutesShare(r: PlayerQuadrantRow): boolean {
  return r.minutes_share != null && r.minutes_share >= MINUTES_SHARE_THRESHOLD;
}

/** usual_position 实测口径(见方案文档第 0 步生产库实测):0=门将 1=后卫
 *  2=中场 3=前锋。 */
export const POSITIONS = [
  { value: 0, label: "门将" },
  { value: 1, label: "后卫" },
  { value: 2, label: "中场" },
  { value: 3, label: "前锋" },
] as const;

export type PositionValue = (typeof POSITIONS)[number]["value"];

export function positionLabel(value: number | null | undefined): string {
  return POSITIONS.find((p) => p.value === value)?.label ?? "未知位置";
}

/** 选中态与排名的身份键,与 teamMetrics.ts::teamKey 同一模式。 */
export function playerKey(p: { player_id?: string | null; name: string }): string {
  return p.player_id ? `id:${p.player_id}` : `name:${p.name}`;
}

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

export const PLAYER_METRICS = {
  npxgPer90: {
    id: "npxg_per90",
    label: "每90分钟非点球xG",
    unit: "",
    digits: 3,
    semantic: "performance",
    caliber: "赛季累计非点球xG ÷ 赛季累计出场分钟数 × 90。不含点球——点球不是靠个人持续创造机会拿到的。",
    value: (r) => num(r.ratios?.npxg_per90?.value),
  },
  finishingDeltaPer90: {
    id: "finishing_delta_per90",
    label: "每90分钟终结超额",
    unit: "",
    digits: 3,
    semantic: "outcome_variance",
    caliber: "每90分钟(非点球进球 − 非点球预期进球)。正值代表射门转化得比预期进球更多,但这不是稳定的终结能力,只是短期窗口的结果记录,不代表未来表现。",
    value: (r) => num(r.ratios?.finishing_delta_per90?.value),
  },
  chancesCreatedPer90: {
    id: "chances_created_per90",
    label: "每90分钟创造机会数",
    unit: "次",
    digits: 2,
    semantic: "performance",
    caliber: "赛季累计创造机会数 ÷ 赛季累计出场分钟数 × 90。只数次数,不看含金量。",
    value: (r) => num(r.ratios?.chances_created_per90?.value),
  },
  xaPer90: {
    id: "xa_per90",
    label: "每90分钟预期助攻(xA)",
    unit: "",
    digits: 3,
    semantic: "performance",
    caliber: "创造的机会按转化概率估算「理论上该有几次助攻」——只有球员维度才有这个字段,球队维度算不出来。",
    value: (r) => num(r.ratios?.xa_per90?.value),
  },
  defensiveActionsPer90: {
    id: "defensive_actions_per90",
    label: "每90分钟防守动作(CBIRT)",
    unit: "次",
    digits: 1,
    semantic: "performance",
    caliber: "每90分钟的抢断+拦截+解围+封堵+回收球——行业标准 Defensive Contribution 口径(FPL/Opta 的 CBIRT),不是本站发明的代理指标。",
    value: (r) => num(r.ratios?.defensive_actions_per90?.value),
  },
  duelWinRate: {
    id: "duel_win_rate",
    label: "对抗成功率",
    unit: "%",
    digits: 1,
    semantic: "performance",
    caliber: "全部对抗(地面+空中)里赢下的比例。数字高不代表身体最强,也可能是挑对抗次数少、赢的都是关键几次。",
    value: (r) => num(r.ratios?.duel_win_rate?.value),
  },
  touchesPer90: {
    id: "touches_per90",
    label: "每90分钟触球数",
    unit: "次",
    digits: 1,
    semantic: "style",
    caliber: "反映球权经手的频率,数字高不直接等于踢得好,可能只是位置靠近球权枢纽。",
    value: (r) => num(r.ratios?.touches_per90?.value),
  },
  progressionRate: {
    id: "progression_rate",
    label: "每百次触球送进前场传球数",
    unit: "次",
    digits: 1,
    semantic: "style",
    caliber: "每一百次触球里,有多少次传球把球送进了前场——数字高说明触球更多转化成向前的推进,不只是倒脚控球。",
    value: (r) => num(r.ratios?.progression_rate?.value),
  },
  xgotFacedPer90: {
    id: "xgot_faced_per90",
    label: "每90分钟面对射正预期进球",
    unit: "",
    digits: 2,
    lowerIsBetter: true,
    semantic: "performance",
    caliber: "门将每90分钟面对的射正球,按落点估算「理论上该丢几个球」——反映承压程度,不是扑救能力本身。",
    value: (r) => num(r.ratios?.xgot_faced_per90?.value),
  },
  goalsPreventedPer90: {
    id: "goals_prevented_per90",
    label: "每90分钟扑救超额",
    unit: "",
    digits: 2,
    semantic: "outcome_variance",
    caliber: "每90分钟(面对射正预期进球 − 实际失球)。正值代表扑救的比预期要多,但这是短期窗口的结果记录,不是稳定的门将能力评价,不代表未来表现。",
    value: (r) => num(r.ratios?.goals_prevented_per90?.value),
  },
} as const satisfies Record<string, PlayerMetricDef>;

export function formatMetric(v: number | null, m: PlayerMetricDef): string {
  if (v == null) return "—";
  return `${v.toFixed(m.digits)}${m.unit}`;
}

/** 分母只数真正有值的球员;全部都缺 → null(与"该球员缺"区分开,面板文案不同)。 */
export function leagueMean(
  rows: PlayerQuadrantRow[],
  m: PlayerMetricDef,
): { mean: number; n: number } | null {
  const vals = rows.map((r) => m.value(r)).filter((v): v is number => v != null);
  if (!vals.length) return null;
  return { mean: vals.reduce((a, b) => a + b, 0) / vals.length, n: vals.length };
}

/**
 * 并列同名次(1224 制):两人并列第 2 时下一人是第 4。
 * lowerIsBetter 时升序排(第 1 = 最小)。values 只应包含真正有值的球员。
 */
export function competitionRank(
  values: number[],
  mine: number,
  lowerIsBetter = false,
): { rank: number; total: number } {
  const better = values.filter((v) => (lowerIsBetter ? v < mine : v > mine)).length;
  return { rank: better + 1, total: values.length };
}

/** 联赛内排名(仅统计画在图上、达标的球员);目标球员自身缺值 → null。 */
export function rankOf(
  rows: PlayerQuadrantRow[],
  m: PlayerMetricDef,
  targetKey: string,
): { rank: number; total: number } | null {
  const scored = rows
    .map((r) => ({ key: playerKey(r.player), v: m.value(r) }))
    .filter((e): e is { key: string; v: number } => e.v != null);
  const me = scored.find((e) => e.key === targetKey);
  if (!me) return null;
  return competitionRank(
    scored.map((e) => e.v),
    me.v,
    m.lowerIsBetter,
  );
}
