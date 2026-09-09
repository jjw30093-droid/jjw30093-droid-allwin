/**
 * 象限图详情面板的取数纯逻辑(2026-09-09):指标定义、联赛均值、联赛内排名。
 *
 * 诚实纪律:
 * - 缺值一律返回 null,不补 0(0 在 xG/零封语境里是有意义的真实值);
 * - 派生指标(点球 xG = 总 xG − 非点球 xG)任一操作数缺失即整体缺失;
 * - 均值与排名的分母只数真正有值的球队,不是 rows.length;
 * - 目标球队自身缺值时排名为 null(不是"排最后")。
 */

import type { TeamSeasonStatRow } from "@/lib/api-v1";

export type MetricDef = {
  id: string;
  label: string;
  unit: string;
  digits: number;
  /** true = 数值越小越好(预期失球 / 犯规 / 牌),排名升序 */
  lowerIsBetter?: boolean;
  /** 赛季累计而非场均(零封场次 / BTTS 场数),面板上要标注口径 */
  perSeason?: boolean;
  value: (r: TeamSeasonStatRow) => number | null;
};

/** 选中态与排名的身份键。不能用对象引用:ECharts 在 setOption 里会克隆
 *  data[i] 的嵌套对象,params.data.pt 与原始对象引用不相等。 */
export function teamKey(t: { team_id?: number | null; name: string }): string {
  return t.team_id != null ? `id:${t.team_id}` : `name:${t.name}`;
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

export function columnMetric(
  column: keyof TeamSeasonStatRow,
  def: Omit<MetricDef, "id" | "value">,
): MetricDef {
  return { id: column, ...def, value: (r) => num(r[column]) };
}

export const METRICS = {
  shotsOnTarget: columnMetric("avg_shots_on_target", { label: "场均射正", unit: "脚", digits: 1 }),
  cleanSheets: columnMetric("clean_sheets", { label: "零封场次", unit: "场", digits: 0, perSeason: true }),
  bttsPct: columnMetric("btts_pct", { label: "双方进球率", unit: "%", digits: 0 }),
  nonPenXg: columnMetric("avg_expected_goals_non_penalty", { label: "场均非点球 xG", unit: "", digits: 2 }),
  corners: columnMetric("avg_corners", { label: "场均角球", unit: "个", digits: 1 }),
  xgot: columnMetric("avg_expected_goals_on_target", { label: "场均射正 xG(xGOT)", unit: "", digits: 2 }),
  penXg: {
    id: "penalty_xg",
    label: "场均点球 xG",
    unit: "",
    digits: 2,
    value: (r) => {
      const total = num(r.avg_expected_goals);
      const nonPen = num(r.avg_expected_goals_non_penalty);
      if (total == null || nonPen == null) return null;
      return Math.max(0, total - nonPen);
    },
  } satisfies MetricDef,
  xgPerShot: {
    id: "xg_per_shot",
    label: "每脚射门 xG",
    unit: "",
    digits: 3,
    value: (r) => {
      const xg = num(r.avg_expected_goals);
      const shots = num(r.avg_total_shots);
      if (xg == null || shots == null || shots <= 0) return null;
      return xg / shots;
    },
  } satisfies MetricDef,
} as const;

export function formatMetric(v: number | null, m: MetricDef): string {
  if (v == null) return "—";
  return `${v.toFixed(m.digits)}${m.unit}`;
}

/** 分母只数真正有值的球队;全联赛都缺 → null(与"该队缺"区分开,面板文案不同)。 */
export function leagueMean(
  rows: TeamSeasonStatRow[],
  m: MetricDef,
): { mean: number; n: number } | null {
  const vals = rows.map((r) => m.value(r)).filter((v): v is number => v != null);
  if (!vals.length) return null;
  return { mean: vals.reduce((a, b) => a + b, 0) / vals.length, n: vals.length };
}

/**
 * 并列同名次(1224 制):两队并列第 2 时下一队是第 4。
 * lowerIsBetter 时升序排(第 1 = 最小)。values 只应包含真正有值的球队。
 */
export function competitionRank(
  values: number[],
  mine: number,
  lowerIsBetter = false,
): { rank: number; total: number } {
  const better = values.filter((v) => (lowerIsBetter ? v < mine : v > mine)).length;
  return { rank: better + 1, total: values.length };
}

/** 联赛内排名;目标球队自身缺值 → null(不是"排最后"),分母只数有值的队。 */
export function rankOf(
  rows: TeamSeasonStatRow[],
  m: MetricDef,
  targetKey: string,
): { rank: number; total: number } | null {
  const scored = rows
    .map((r) => ({ key: teamKey(r.team), v: m.value(r) }))
    .filter((e): e is { key: string; v: number } => e.v != null);
  const me = scored.find((e) => e.key === targetKey);
  if (!me) return null;
  return competitionRank(
    scored.map((e) => e.v),
    me.v,
    m.lowerIsBetter,
  );
}
