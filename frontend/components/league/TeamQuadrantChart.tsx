"use client";

/**
 * 球队象限图 —— 用联赛平均线把散点切成四象限,每个象限直接给中文标签
 * ("能创造也能防守"而不是"高 xG 低 xGA")。
 *
 * 解决的问题:此前 /league/[id]/team-stats 只有 5 张纯文本 top10 榜,
 * 一个 20 队联赛里 **第 11–20 名完全不可见**;而 silver_team_season_stats
 * 的 xG 拆解列(运动战/定位球/非点球,767 行全非空)前端零消费。
 *
 * 三个视角都可点选(CLAUDE.md §11.2 的"可交互"要求):
 *   攻防 —— 每场创造 xG × 每场被创造 xG(y 轴反转:越靠上防守越好)
 *   战术 —— 运动战 xG × 定位球 xG(全站独一份的战术叙事素材)
 *   出手 —— 每场射门数 × 每场 xG(量 vs 质:多而不精 / 少而精)
 *
 * 诚实纪律:
 * - 缺数据的球队直接不画,不补 0(0 在 xG 语境里是"一次机会都没创造",
 *   是有意义的真实值,不能拿来当缺失占位)。
 * - 攻防视角依赖 fact_league_table 的 xg 档,并非每个联赛赛季都有;
 *   数据不足时该视角禁用并写明原因,不静默回退。
 * - 参考线是**本联赛本赛季**的平均值,不是跨联赛基准 —— 摘要里说清楚。
 */

import { useMemo, useState } from "react";
import type { EChartsOption } from "echarts";
import { EChart } from "@/components/EChart";
import { useChartColors } from "@/components/charts/useChartColors";
import type { TeamSeasonStatRow } from "@/lib/api-v1";
import styles from "./TeamQuadrantChart.module.css";

type Axis = {
  key: keyof TeamSeasonStatRow;
  label: string;
  unit: string;
  digits: number;
  /** true 表示"数值越小越好",用于象限命名与 y 轴反转 */
  lowerIsBetter?: boolean;
};

type View = {
  id: string;
  tab: string;
  title: string;
  x: Axis;
  y: Axis;
  /** 四象限中文名,顺序按**好/差**:x好y好 / x差y好 / x差y差 / x好y差
   *  (不是高/低 —— "每场被创造 xG"越低越好,见 quadrantOf) */
  quadrants: [string, string, string, string];
  note: string;
};

const VIEWS: View[] = [
  {
    id: "both-ends",
    tab: "攻防",
    title: "进攻创造 × 防守让出",
    x: { key: "avg_expected_goals", label: "每场创造 xG", unit: "", digits: 2 },
    y: {
      key: "avg_expected_goals_conceded",
      label: "每场被创造 xG",
      unit: "",
      digits: 2,
      lowerIsBetter: true,
    },
    quadrants: ["两头都强", "守强攻弱", "两头都弱", "对攻型"],
    note: "横轴是本队每场制造出多少质量的射门机会，纵轴是对手在本队门前每场拿到多少（已反转，越靠上防守越好）。",
  },
  {
    id: "tactics",
    tab: "战术",
    title: "运动战 × 定位球",
    x: {
      key: "avg_expected_goals_open_play",
      label: "运动战 xG / 场",
      unit: "",
      digits: 2,
    },
    y: {
      key: "avg_expected_goals_set_play",
      label: "定位球 xG / 场",
      unit: "",
      digits: 2,
    },
    quadrants: ["两条路都通", "吃定位球", "两条路都窄", "阵地战为主"],
    note: "运动战 xG 来自流畅进攻，定位球 xG 来自角球/任意球/界外球后的机会。两项相加约等于非点球 xG，剩下的是点球。",
  },
  {
    id: "volume",
    tab: "出手",
    title: "射门数量 × 机会质量",
    x: { key: "avg_total_shots", label: "每场射门数", unit: "脚", digits: 1 },
    y: { key: "avg_expected_goals", label: "每场 xG", unit: "", digits: 2 },
    quadrants: ["又多又好", "少而精", "又少又差", "多而不精"],
    note: "同样的射门数，xG 越高说明出手位置越好。右下角是打得多但位置差，左上角是出手少但每次都在好位置。",
  },
];

export type Pt = { name: string; x: number; y: number; mp: number | null };

/** collectPoints 只需要两根轴的列名;View 结构上满足它,测试可以只传列名。 */
export type __TestView = {
  x: { key: keyof TeamSeasonStatRow };
  y: { key: keyof TeamSeasonStatRow };
};

export function collectPoints(rows: TeamSeasonStatRow[], view: __TestView): Pt[] {
  const out: Pt[] = [];
  for (const r of rows) {
    const x = r[view.x.key];
    const y = r[view.y.key];
    // 缺一个维度就整点丢弃 —— 半个坐标画不出散点,补 0 会造出假的"极端球队"
    if (typeof x !== "number" || typeof y !== "number") continue;
    out.push({ name: r.team.name, x, y, mp: r.matches_played ?? null });
  }
  return out;
}

const mean = (nums: number[]) => nums.reduce((a, b) => a + b, 0) / nums.length;

/**
 * 点落在哪个象限,索引按**好/差**而不是高/低:
 *   0 = x 好 y 好, 1 = x 差 y 好, 2 = x 差 y 差, 3 = x 好 y 差
 *
 * y 轴必须传 lowerIsBetter —— "每场被创造 xG"越小越好,若按数值高低命名,
 * 真正攻守兼备的球队会被贴上"对攻型",和 colorOf 的配色正好互相打架。
 */
export function quadrantOf(
  p: Pt,
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

function fmt(v: number, a: Axis) {
  return `${v.toFixed(a.digits)}${a.unit}`;
}

/**
 * 只给"离联赛平均最远"的几支球队标队名。
 *
 * 手机 375px 宽塞 20 个中文队名必然叠成一团(实测:中游 7 支互相压字、
 * 右边缘还会被裁)。极值球队才是这张图要讲的东西,中游球队点开有 tooltip、
 * 名字也一个不少地列在下方分组清单里 —— 所以这是取舍展示密度,不是丢数据。
 *
 * 距离按各轴标准差归一化,否则量纲大的轴(射门数 ~12)会完全压过小的(xG ~1.4)。
 */
export function outlierNames(pts: Pt[], mx: number, my: number, take = 6): Set<string> {
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

export function TeamQuadrantChart({ rows }: { rows: TeamSeasonStatRow[] }) {
  const c = useChartColors();
  // 攻防视角依赖数据源的 xg 档,不是每个联赛赛季都有 —— 先算可用性再定默认视角
  const available = useMemo(
    () => VIEWS.filter((v) => collectPoints(rows, v).length >= 4),
    [rows],
  );
  const [viewId, setViewId] = useState<string | null>(null);
  const view = available.find((v) => v.id === viewId) ?? available[0];

  const pts = useMemo(() => (view ? collectPoints(rows, view) : []), [rows, view]);

  if (!view || pts.length < 4) {
    return (
      <p className={styles.empty}>
        该联赛该赛季的球队指标不足以绘制象限图（有效球队少于 4 支）。
      </p>
    );
  }

  const mx = mean(pts.map((p) => p.x));
  const my = mean(pts.map((p) => p.y));

  // 象限索引与配色共用同一个判定,避免"颜色说好、标签说差"的自相矛盾:
  // 0(两项都好)= 绿, 2(两项都差)= 红, 1/3(一好一差)= 金。
  const lowY = view.y.lowerIsBetter === true;
  const quad = (p: Pt) => quadrantOf(p, mx, my, lowY);
  const QUAD_COLOR = [c.win, c.teal, c.loss, c.teal];
  const colorOf = (p: Pt) => QUAD_COLOR[quad(p)];
  const labelled = outlierNames(pts, mx, my);

  const sampleNote = (() => {
    const mps = pts.map((p) => p.mp).filter((m): m is number => m != null);
    if (!mps.length) return "";
    const lo = Math.min(...mps);
    const hi = Math.max(...mps);
    return lo === hi ? `每队 ${lo} 场` : `每队 ${lo}–${hi} 场`;
  })();

  const option: EChartsOption = {
    // top 留 26 给 y 轴名(挂在轴顶),bottom 留 44 给 x 轴名
    grid: { left: 46, right: 26, top: 26, bottom: 44 },
    xAxis: {
      type: "value",
      name: view.x.label,
      nameLocation: "middle",
      nameGap: 26,
      nameTextStyle: { color: c.ink2, fontSize: 12 },
      // scale + 百分比留白:不硬写 min/max,否则 ECharts 会把
      // 0.47236656243863856 这种原始端点值直接画成刻度标签。
      scale: true,
      boundaryGap: ["14%", "14%"],
      axisLabel: { color: c.ink2, fontSize: 12 },
      splitLine: { lineStyle: { opacity: 0.1 } },
    },
    yAxis: {
      type: "value",
      name: view.y.label,
      // inverse 会把轴的 end 翻到底部,和 x 轴名撞在一起 —— 反转时改用 start,
      // 让轴名永远停在图的左上角。
      nameLocation: lowY ? "start" : "end",
      nameGap: 12,
      nameTextStyle: { color: c.ink2, fontSize: 12, align: "left" },
      // 越少被创造 xG 越好 → 反转,让"好"永远在上方
      inverse: lowY,
      scale: true,
      boundaryGap: ["16%", "16%"],
      axisLabel: { color: c.ink2, fontSize: 12 },
      splitLine: { lineStyle: { opacity: 0.1 } },
    },
    tooltip: {
      confine: true,
      trigger: "item",
      formatter: (p: unknown) => {
        const d = (p as { data: { pt: Pt } }).data.pt;
        const q = view.quadrants[quad(d)];
        return (
          `<b>${d.name}</b>（${q}）<br/>` +
          `${view.x.label} ${fmt(d.x, view.x)}（联赛均值 ${fmt(mx, view.x)}）<br/>` +
          `${view.y.label} ${fmt(d.y, view.y)}（联赛均值 ${fmt(my, view.y)}）` +
          (d.mp != null ? `<br/>样本 ${d.mp} 场` : "")
        );
      },
    },
    series: [
      {
        type: "scatter",
        symbolSize: 13,
        data: pts.map((p) => ({
          value: [p.x, p.y],
          pt: p,
          itemStyle: { color: colorOf(p), opacity: 0.9 },
        })),
        label: {
          show: true,
          // 标在点正上方而不是右侧:右侧的队名到了图右边缘会被裁掉
          position: "top",
          distance: 5,
          color: c.ink2,
          fontSize: 12,
          formatter: (p: unknown) => {
            const n = (p as { data: { pt: Pt } }).data.pt.name;
            return labelled.has(n) ? n : "";
          },
        },
        // 中游球队会挤成一团:先上下微调错开(moveOverlap),仍然压在一起的
        // 才隐藏。被隐藏的只是文字,点还在图上,队名在下方分组清单里一个不少。
        labelLayout: { moveOverlap: "shiftY", hideOverlap: true },
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { color: c.ink2, type: "dashed", opacity: 0.45 },
          label: { show: false },
          data: [{ xAxis: mx }, { yAxis: my }],
        },
      },
    ],
  };

  const byQuadrant = view.quadrants.map((label, i) => ({
    label,
    teams: pts.filter((p) => quad(p) === i).map((p) => p.name),
  }));

  const ariaSummary =
    `${view.title}象限图，虚线是本联赛本赛季平均值（${view.x.label} ${fmt(mx, view.x)}，` +
    `${view.y.label} ${fmt(my, view.y)}）。` +
    byQuadrant
      .filter((q) => q.teams.length)
      .map((q) => `${q.label}：${q.teams.join("、")}`)
      .join("；") +
    `。共 ${pts.length} 支球队${sampleNote ? `，${sampleNote}` : ""}。` +
    `参考线是联赛内部平均，不能拿来跨联赛比较。`;

  return (
    <section className={styles.card}>
      <header className={styles.head}>
        <div>
          <h2 className={styles.title}>球队象限图</h2>
          <p className={styles.sub}>
            {view.title} · 虚线为本联赛本赛季平均
            {sampleNote ? ` · ${sampleNote}` : ""}
          </p>
        </div>
        <div className={styles.tabs} role="tablist" aria-label="象限图视角">
          {VIEWS.map((v) => {
            const usable = available.some((a) => a.id === v.id);
            return (
              <button
                key={v.id}
                type="button"
                role="tab"
                aria-selected={v.id === view.id}
                disabled={!usable}
                title={usable ? undefined : "该联赛该赛季缺少此视角所需的数据"}
                className={v.id === view.id ? styles.tabOn : styles.tab}
                onClick={() => setViewId(v.id)}
              >
                {v.tab}
              </button>
            );
          })}
        </div>
      </header>

      {/* 球队越多越需要纵向空间给队名错开,20 队 → 380 */}
      <EChart option={option} height={Math.max(340, pts.length * 19)} ariaSummary={ariaSummary} />

      <div className={styles.legend}>
        {byQuadrant
          .filter((q) => q.teams.length)
          .map((q) => (
            <div key={q.label} className={styles.legendRow}>
              <span className={styles.legendKey}>{q.label}</span>
              <span className={styles.legendVal}>{q.teams.join("、")}</span>
            </div>
          ))}
      </div>

      <p className={styles.note}>
        {view.note} 图上只标出离联赛平均最远的几支球队，中游球队点一下可以看名字，
        完整名单见上方分组。
        {available.length < VIEWS.length && (
          <>
            {" "}
            灰掉的视角是该联赛该赛季数据源没有提供对应指标，不是本站算不出来。
          </>
        )}
      </p>
    </section>
  );
}
