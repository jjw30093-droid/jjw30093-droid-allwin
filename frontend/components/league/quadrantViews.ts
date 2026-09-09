/**
 * 联赛球队象限图的视角定义与纯逻辑(2026-09-09 从 TeamQuadrantChart.tsx 抽出,
 * 避免 "use client" 组件与 option 构造器循环引用;组件只剩接线)。
 *
 * 三个视角:
 *   攻防 —— 场均预期进球 xG × 场均预期失球 xGA(y 轴反转:越靠上防守越好)
 *   战术 —— 运动战 xG × 定位球 xG(全站独一份的战术叙事素材)
 *   射门质量 —— 场均射门数 × 场均预期进球(量 vs 质:广种薄收 / 少而精)
 *
 * 文案纪律(2026-09-09 术语校准):象限名一律用中文足球术语("攻守兼备"
 * "对攻型""广种薄收"),不用篮球词("出手")、不用口语("两头都强""两条路都通");
 * 射门多而 xG 低通常是远射多,但没有射门距离数据,所以用"广种薄收"描述现象,
 * 不写"远射为主"去断言原因。
 *
 * 诚实纪律:
 * - 缺数据的球队直接不画,不补 0(0 在 xG 语境里是"一次机会都没创造",
 *   是有意义的真实值,不能拿来当缺失占位)。
 * - 攻防视角依赖 fact_league_table 的 xg 档,并非每个联赛赛季都有;
 *   数据不足时该视角禁用并写明原因,不静默回退。
 * - 参考线是**本联赛本赛季**的平均值,不是跨联赛基准 —— 摘要里说清楚。
 */

import type { TeamSeasonStatRow } from "@/lib/api-v1";
import { METRICS, teamKey, type MetricDef } from "./teamMetrics";

export { teamKey };

export type Axis = {
  key: keyof TeamSeasonStatRow;
  label: string;
  unit: string;
  digits: number;
  /** true 表示"数值越小越好",用于象限命名与 y 轴反转 */
  lowerIsBetter?: boolean;
};

export type View = {
  id: string;
  tab: string;
  title: string;
  x: Axis;
  y: Axis;
  /** 四象限中文名,顺序按**好/差**:x好y好 / x差y好 / x差y差 / x好y差
   *  (不是高/低 —— "场均预期失球"越低越好,见 quadrantOf) */
  quadrants: [string, string, string, string];
  note: string;
  /** 详情面板里跟这个视角相关的补充指标(各自带联赛排名),不堆全部 17 项 */
  related: MetricDef[];
};

export const VIEWS: View[] = [
  {
    id: "both-ends",
    tab: "攻防",
    title: "预期进球 × 预期失球",
    x: { key: "avg_expected_goals", label: "场均预期进球 xG", unit: "", digits: 2 },
    y: {
      key: "avg_expected_goals_conceded",
      label: "场均预期失球 xGA",
      unit: "",
      digits: 2,
      lowerIsBetter: true,
    },
    quadrants: ["攻守兼备", "重守轻攻", "攻守俱弱", "对攻型"],
    note: "横轴是本队每场制造出多少质量的射门机会（预期进球），纵轴是对手在本队门前每场拿到多少（预期失球，已反转，越靠上防守越好）。",
    related: [METRICS.shotsOnTarget, METRICS.cleanSheets, METRICS.bttsPct],
  },
  {
    id: "tactics",
    tab: "战术",
    title: "运动战 × 定位球",
    x: { key: "avg_expected_goals_open_play", label: "场均运动战 xG", unit: "", digits: 2 },
    y: { key: "avg_expected_goals_set_play", label: "场均定位球 xG", unit: "", digits: 2 },
    quadrants: ["双线开花", "依赖定位球", "进攻乏术", "运动战主导"],
    note: "运动战 xG 来自流畅进攻，定位球 xG 来自角球/任意球/界外球后的机会。两项相加约等于非点球 xG，剩下的是点球。",
    related: [METRICS.nonPenXg, METRICS.penXg, METRICS.corners],
  },
  {
    id: "volume",
    tab: "射门质量",
    title: "射门数量 × 机会质量",
    x: { key: "avg_total_shots", label: "场均射门数", unit: "脚", digits: 1 },
    y: { key: "avg_expected_goals", label: "场均预期进球 xG", unit: "", digits: 2 },
    quadrants: ["量质齐优", "少而精", "量质皆低", "广种薄收"],
    note: "同样的射门数，预期进球越高说明射门位置越好。右下角是打得多但位置差，左上角是射门少但每次都在好位置。",
    related: [METRICS.shotsOnTarget, METRICS.xgot, METRICS.xgPerShot],
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

/** collectPoints 只需要两根轴的列名;View 结构上满足它,测试可以只传列名。 */
export type __TestView = {
  x: { key: keyof TeamSeasonStatRow };
  y: { key: keyof TeamSeasonStatRow };
};

export function collectPoints(rows: TeamSeasonStatRow[], view: __TestView): Pt[] {
  const out: Pt[] = [];
  const seen = new Set<string>();
  for (const r of rows) {
    const x = r[view.x.key];
    const y = r[view.y.key];
    // 缺一个维度就整点丢弃 —— 半个坐标画不出散点,补 0 会造出假的"极端球队"
    if (typeof x !== "number" || typeof y !== "number") continue;
    // 同一支球队出现多行(本地测试库曾出现 16 行重复的 1001)只画第一行:
    // key 是 React key 也是选中身份,重复会让 16 个队徽叠在一处且无法选中
    const key = teamKey(r.team);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({
      key,
      name: r.team.name,
      x,
      y,
      mp: r.matches_played ?? null,
      crestUrl: r.team.crest_url ?? null,
      teamId: r.team.team_id ?? null,
    });
  }
  return out;
}

export const mean = (nums: number[]) => nums.reduce((a, b) => a + b, 0) / nums.length;

/**
 * 点落在哪个象限,索引按**好/差**而不是高/低:
 *   0 = x 好 y 好, 1 = x 差 y 好, 2 = x 差 y 差, 3 = x 好 y 差
 *
 * y 轴必须传 lowerIsBetter —— "场均预期失球"越小越好,若按数值高低命名,
 * 真正攻守兼备的球队会被贴上"对攻型",和配色正好互相打架。
 */
export function quadrantOf<P extends { x: number; y: number }>(
  p: P,
  mx: number,
  my: number,
  lowerIsBetterY = false,
): number {
  const xGood = p.x >= mx;
  const yGood = lowerIsBetterY ? p.y < my : p.y >= my;
  if (xGood && yGood) return 0;
  if (!xGood && yGood) return 1;
  if (!xGood && !yGood) return 2;
  return 3;
}

export function fmt(v: number, a: Axis) {
  return `${v.toFixed(a.digits)}${a.unit}`;
}

export function axisToMetric(a: Axis): MetricDef {
  return {
    id: a.key,
    label: a.label,
    unit: a.unit,
    digits: a.digits,
    lowerIsBetter: a.lowerIsBetter,
    value: (r) => (typeof r[a.key] === "number" ? (r[a.key] as number) : null),
  };
}

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

export const MAX_SELECTED = 2;

/** 点击切换:已选 → 移除;未选且不足上限 → 追加;满员 → FIFO 顶掉最早的。 */
export function toggleSelection(keys: string[], key: string, max = MAX_SELECTED): string[] {
  if (keys.includes(key)) return keys.filter((k) => k !== key);
  const next = [...keys, key];
  return next.length > max ? next.slice(next.length - max) : next;
}

/** 挡陈旧选中(照抄 ShotMapChart.resolveSelectedShot 的派生态做法):只保留当前视角仍在图上的 key,顺序按选中先后。 */
export function resolveSelectedTeams(pts: Pt[], keys: string[]): Pt[] {
  const out: Pt[] = [];
  for (const k of keys) {
    const p = pts.find((q) => q.key === k);
    if (p) out.push(p);
  }
  return out;
}

/** ECharts 点击参数 → 选中键。优先 dataIndex(不经过克隆路径),payload 兜底;点空白返回 null。 */
export function resolveClickedKey(params: unknown, pts: Pt[]): string | null {
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
