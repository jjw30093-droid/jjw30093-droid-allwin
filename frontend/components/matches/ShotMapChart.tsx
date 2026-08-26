"use client";

/**
 * 单场射门图(ECharts custom 系列,唯一图表库,复用底层 EChart 封装;不进
 * SpecCharts——那是 analysis_bundle.chart_specs 的封闭类型联合)。
 *
 * 2026-08-25 标记形状改版(对齐 FotMob):进球=足球图案、射正=实心圆、
 * 未射正(偏出/中框/被封堵)=空心圈,不再用"描边粗细"区分。两条 scatter
 * 合并成一条 custom 系列(进球排最后画在最上层);hover tooltip 整块删除
 * (与点击详情面板同信息两套渲染,手机上还是伪交互),可发现性由球场下方
 * 常驻提示行承担。
 *
 * 坐标约定(30 场真实抽样验证的 FotMob 原始数据行为,详见
 * backend/queries/match_report.py 模块注释):主客队射门都朝同一端(x→105)
 * 记录。展示层把客队镜像到左半场(x→105-x, y→68-y),主队保持攻向右——
 * 不镜像的话两队射门会全部堆在同一半场,是错误的图。
 * 点球大战(Period='PenaltyShootout')排除出图与 xG 合计(不属于比赛 xG)。
 * 图表懒挂载:容器首次进入视口才 init ECharts(hidden tab 里 0×0 容器
 * init 会告警);文字摘要(§11.2 硬性要求)始终在 DOM。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type { EChartsOption, CustomSeriesRenderItemReturn } from "echarts";
import { EChart } from "@/components/EChart";
import type { ChartMode } from "@/components/charts/chartMode";
import type { ChartColors } from "@/components/charts/useChartColors";
import { useChartColors } from "@/components/charts/useChartColors";
import { resolveMatchColors, type TeamColorPair } from "@/components/charts/matchTeamColors";
import type { MatchReportResponse } from "@/lib/api-v1";
import { SHOT_OUTCOME_ZH, SHOT_SITUATION_ZH, SHOT_TYPE_ZH } from "@/components/matches/zh";
import { FootballPitchBackground } from "./FootballPitchBackground";
import { ShotDetailPanel } from "./ShotDetailPanel";
import styles from "./ShotMapChart.module.css";

type MatchReport = Extract<MatchReportResponse, { available: true }>;
type Shot = MatchReport["shots"][number];

const PITCH_LEN = 105;
const PITCH_WID = 68;

function symbolSize(xg: number | null | undefined): number {
  return 7 + Math.min(1, xg ?? 0) * 22;
}

/** AttemptSaved 精确结果文案:is_blocked 已知时能区分"被封堵"和"被扑出";
 * 未知(该场未回填,2026-08-23 起才采集,见 backend/migrations/core/
 * 0005_shotmap_raw_fields.sql)时退回 SHOT_OUTCOME_ZH 的"被扑/被挡"。 */
export function outcomeLabelFor(s: Shot): string {
  if (s.outcome === "AttemptSaved" && s.is_blocked != null) {
    return s.is_blocked ? "被封堵" : "被扑出";
  }
  return SHOT_OUTCOME_ZH[s.outcome ?? ""] ?? s.outcome ?? "";
}

type SideFilter = "both" | "home" | "away";
type OutcomeFilter = "all" | "on_target" | "goal";
type HalfFilter = "all" | "first" | "second";

/** 图上真正用到的射门情境(库里 8 种,这里只列竞彩用户会主动筛的几种)。 */
const SITUATION_CHIPS = ["FromCorner", "SetPiece", "FreeKick", "FastBreak", "Penalty"];
const BODY_PARTS = ["LeftFoot", "RightFoot", "Header"];

/** 射正 = 进球 + 被扑出(中框/偏出不算射正,与球队统计口径一致)。
 *
 * is_blocked 已知时(该场已回填,见 fact_shotmap.Is_Blocked)用精确口径:
 * AttemptSaved 里真正被封堵的球排除掉——is_on_target 本身**不**排除被封堵
 * 的球(FotMob 把"射门轨迹朝不朝门"与"是否被封堵"分开标记,实测同一脚
 * AttemptSaved 里被封堵的球 99.8% 仍标 is_on_target=true),必须
 * is_blocked=false 才算真正射正。未回填的场次(is_blocked 为 null)退回
 * 旧的 Outcome 口径(混入被封堵球,与官方统计对不上,但没有更好的数据)。 */
function isOnTarget(s: Shot): boolean {
  if (s.outcome === "Goal") return true;
  if (s.outcome !== "AttemptSaved") return false;
  return s.is_blocked == null ? true : !s.is_blocked;
}

/* ── 标记形状(2026-08-25,纯函数抽出供测试直接断言,CLAUDE.md §11.3) ── */

export type MarkerKind = "goal" | "on_target" | "off_target";

/** 结果 → 标记形态。射正口径复用既有 isOnTarget(含 is_blocked 精确/退化
 * 两档)——图上的形状语义必须与"射正"筛选按钮同一口径,不另造第二套。 */
export function markerKindFor(s: Shot): MarkerKind {
  if (s.outcome === "Goal") return "goal";
  return isOnTarget(s) ? "on_target" : "off_target";
}

/** 单位圆坐标系(-1..1)下的足球图案:中心五边形 + 5 块边缘楔形。
 * 调用方按 cx/cy/r 缩放。纯几何,零依赖,可单测。顶点朝上(-90°),
 * 楔形落在五边形顶点之间的间隙方向(+36° 相位),是经典 2D 足球剪影。 */
export function ballPatchPolygons(): [number, number][][] {
  const polys: [number, number][][] = [];
  const deg = (d: number) => (d * Math.PI) / 180;
  // 中心五边形(半径 0.42)
  const pentagon: [number, number][] = [];
  for (let k = 0; k < 5; k++) {
    const a = deg(-90 + k * 72);
    pentagon.push([0.42 * Math.cos(a), 0.42 * Math.sin(a)]);
  }
  polys.push(pentagon);
  // 5 块边缘楔形(内沿 r=0.66 宽、外沿 r=0.98 窄的等腰梯形)
  for (let k = 0; k < 5; k++) {
    const a = deg(-90 + 36 + k * 72);
    const wedge: [number, number][] = [
      [0.66 * Math.cos(a - deg(22)), 0.66 * Math.sin(a - deg(22))],
      [0.66 * Math.cos(a + deg(22)), 0.66 * Math.sin(a + deg(22))],
      [0.98 * Math.cos(a + deg(13)), 0.98 * Math.sin(a + deg(13))],
      [0.98 * Math.cos(a - deg(13)), 0.98 * Math.sin(a - deg(13))],
    ];
    polys.push(wedge);
  }
  return polys;
}

/** 进球必须有可读最小直径:xG=0.03 的进球按 symbolSize() 只有 7.7px,
 * 球形图案在那个尺寸下不可辨识。空心圈也要下限,否则 2px 环几乎糊死。 */
const GOAL_MIN_D = 18;
const RING_MIN_D = 9;
export function markerRadius(s: Shot, kind: MarkerKind): number {
  const d = symbolSize(s.xg);
  return Math.max(d, kind === "goal" ? GOAL_MIN_D : RING_MIN_D) / 2;
}

/** 进球排最后 → custom 系列按 dataIndex 顺序绘制,进球画在最上层
 * (scatter 双系列时代做不到)。Array.sort 是稳定排序,非进球相对顺序不变。
 * buildOption 与组件的点击处理都必须用这同一个排序,dataIndex 才对得上。 */
export function orderShotsForRender(plotted: Shot[]): Shot[] {
  return [...plotted].sort(
    (a, b) => Number(markerKindFor(a) === "goal") - Number(markerKindFor(b) === "goal"),
  );
}

/** 2026-08-26 真实生产事故修复:真实浏览器里 ECharts custom 系列在内部
 * setOption 时会克隆 `data[i]` 里嵌套的非原始值——点击事件 `params.data.shot`
 * 拿到的是一份结构相同但**引用不同**的副本,不是 buildOption 传入的那个
 * 原始 shot 对象。`frontend/tests/shot-map-click.test.ts` 用 zrender 内部
 * `handler.dispatch()` 直接派发事件,绕开了真实 `chart.setOption()` 走的
 * 那条克隆路径,所以此前一直是绿的——测试通过不代表真实浏览器行为一致,
 * 这条差异只有真实点击 + 真实 setOption 才会暴露(线上复现:点击任意射门
 * 标记,详情面板完全不出现,`resolveClickedShot` 能正确解析出射门对象,
 * 但下游 `resolveSelectedShot` 的引用相等判断永远找不到它)。
 *
 * 因此确认"点中的是 shots 系列"(`seriesName === "shots"`)之后,不能再
 * 信任 `params.data.shot` 这个引用本身,定位改走 `dataIndex`——它是 zrender
 * 按数据下标关联点击事件的核心机制,不涉及嵌套对象克隆,可靠。调用方必须
 * 传入与 buildOption 相同的 orderShotsForRender 结果,dataIndex 才对得上号。
 * `.shot` 仍保留为兜底(不要求 seriesName——历史调用约定,`.shot` 字段本身
 * 只会出现在 shots 系列自己的 data 项上,不会和其它系列混淆),覆盖调用方
 * 手工构造、不带 seriesName/dataIndex 的场景(如本文件测试里的部分用例)。 */
export function resolveClickedShot(params: unknown, ordered: Shot[]): Shot | null {
  const p = params as { data?: { shot?: Shot }; dataIndex?: number; seriesName?: string };
  if (p?.seriesName === "shots" && typeof p?.dataIndex === "number") {
    const byIndex = ordered[p.dataIndex];
    if (byIndex) return byIndex;
  }
  return p?.data?.shot ?? null;
}

/**
 * 纯筛选:只做"少显示一些",绝不改动任何一次射门的数值。
 * 统计数字随筛选结果重算 —— 筛选后的 xG 合计就是所选子集的合计,
 * 不是全场合计,摘要里会说明当前口径。
 */
/** 主客队射门在原始数据里都朝同一端(x→105)记录,展示层把客队镜像到
 * 左半场——这是"原始坐标系里任意一个点"的通用变换,不只用于射门起点,
 * 轨迹线终点(球门线穿越点/封堵点)同样在这个原始坐标系里,用同一个函数
 * 镜像,不为终点另外推一套公式。 */
function mirrorPoint(isHome: boolean, x: number, y: number): [number, number] {
  return isHome ? [x, y] : [PITCH_LEN - x, PITCH_WID - y];
}

/** 球门宽 7.32m,中心 y=34(与 backend/queries/match_report.py 模块注释同一
 * 口径实测验证过)。 */
const GOAL_CENTER_Y = 34;

/** 2026-08-24 画射门轨迹线:原始(未镜像)坐标系下的终点,null = 没有可靠
 * 终点数据,调用方静默不画线。
 *
 * 优先级:
 *  1. is_blocked 且 blocked_x/blocked_y 均非空 → 封堵点(真实坐标)。
 *  2. 否则 outcome==='Goal' 或 is_on_target===true:
 *     a. goal_crossed_y 非空且在球场宽度域 [0,68] 内 → 真实球门线穿越点
 *        (x=105,y=goal_crossed_y)。语义 2026-08-25 已对生产 fact_shotmap
 *        数值验证:goal_crossed_y 就是球场 Y 坐标(球门跨 30.34..37.66,
 *        射正样本全部落在框内),这是**已验证映射**,与 GoalMouthDiagram
 *        画的入网位置同源,不会出现"轨迹指正中、球门框图指左下角"的自相
 *        矛盾;
 *     b. goal_crossed_y 缺失(旧场次未采集)→ **兜底**退化到球门正中
 *        (x=105,y=34)——轨迹线需要一个终点才能画,兜底不是验证过的
 *        真实落点,只是"朝门方向"的示意。
 *  3. 否则(未被封堵的非射正球)→ null,没有可信终点。
 */
export function trajectoryEndpoint(
  s: Shot,
): { x: number; y: number; blocked: boolean } | null {
  if (s.is_blocked && s.blocked_x != null && s.blocked_y != null) {
    return { x: s.blocked_x, y: s.blocked_y, blocked: true };
  }
  if (s.outcome === "Goal" || s.is_on_target === true) {
    if (s.goal_crossed_y != null && s.goal_crossed_y >= 0 && s.goal_crossed_y <= PITCH_WID) {
      return { x: PITCH_LEN, y: s.goal_crossed_y, blocked: false };
    }
    return { x: PITCH_LEN, y: GOAL_CENTER_Y, blocked: false };
  }
  return null;
}

/** 射门的稳定结构化标识(2026-08-26)。`shot_id` 来源侧经常缺失(见调试
 * 实测,历史场次普遍为 null),不能单独作为 key;球员+分钟+半场+落点坐标+
 * 结果的组合在同一场比赛里不会撞(同一球员同一分钟不可能两次射门落在
 * 完全相同的坐标又是相同结果)。 */
function shotKey(s: Shot): string {
  return `${s.player_id}|${s.minute}|${s.period}|${s.x}|${s.y}|${s.outcome}`;
}

/** 筛选变化导致选中射门不在新的 plotted 里时返回 null——这是唯一的处理点,
 * 组件渲染和测试都只需要认这一个函数。用派生状态而非 useEffect 清空:
 * selected 只记"最后一次点击/翻页选中的是哪个 shot 对象",真正参与渲染的
 * 永远是这个函数的返回值——plotted 一变,不在其中的选中项自动"隐形",
 * 切筛选筛没了、又切回来会重新出现,这是刻意的简化。
 *
 * 2026-08-26 真实生产事故修复:曾经用 `plotted.includes(selected)` 做引用
 * 相等判断——真实浏览器里 ECharts 会克隆 custom 系列 data 里嵌套的 shot
 * 对象(见 resolveClickedShot 注释),点击拿到的对象即使已经改用 dataIndex
 * 路径整体规避了这个问题,这里仍然改用结构化 key 比较而不是引用比较,是
 * 双保险——不让"选中判断依赖对象引用是否被下游意外克隆"这一类问题在未来
 * 任何新代码路径里重演。返回的是 plotted 里那个真实引用(不是 selected
 * 本身),保证下游拿到的对象与图上正在渲染的对象是同一个。 */
export function resolveSelectedShot(plotted: Shot[], selected: Shot | null): Shot | null {
  if (!selected) return null;
  const key = shotKey(selected);
  return plotted.find((s) => shotKey(s) === key) ?? null;
}

export interface ShotSideSummary {
  /** 该侧画在图上的射门点数(含该队球员打进自家球门的乌龙球点)。 */
  n: number;
  /** 该队进球数,按**受益方**计:本队非乌龙进球 + 对方球员的乌龙球。
   * FotMob 把乌龙球记在"打进自家球门那一队"名下,直接按 is_home 数
   * Goal 会归错队(对照实验 400 场含乌龙球比赛错 392 场)。 */
  goals: number;
  /** 计入受益方球数的乌龙球数(来自对方球员),>0 时摘要里如实说明。 */
  ownGoalsBenefited: number;
  /** 有 xG 值的射门的合计;该侧没有任何带 xG 的射门时为 null(不伪装成 0,
   * 与 MatchDataModules.tsx 引用 CLAUDE.md §6.2 的既有纪律一致)。 */
  xg: number | null;
  /** xG 缺失且非乌龙球的射门数(乌龙球 xG 缺失是已知固定行为,单独口径;
   * 这里只统计"正常射门却没有 xG"的异常缺失,>0 时摘要里说明)。 */
  missingXg: number;
}

/** 2026-08-24 抽出为可独立测试的纯函数(CLAUDE.md §11.3 新增纪律:图上的
 * 聚合数字必须可独立断言,不能只活在组件渲染路径里)。三个数字(次数/球数/
 * xG 合计)全部取自同一个 plotted 集合——筛选变化时同步变化。 */
export function summarizeSide(plotted: Shot[], isHome: boolean): ShotSideSummary {
  const own = plotted.filter((s) => s.is_home === isHome);
  const regularGoals = own.filter((s) => s.outcome === "Goal" && !s.is_own_goal).length;
  const ownGoalsBenefited = plotted.filter(
    (s) => s.is_home !== isHome && s.outcome === "Goal" && s.is_own_goal,
  ).length;
  const withXg = own.filter((s) => s.xg != null);
  return {
    n: own.length,
    goals: regularGoals + ownGoalsBenefited,
    ownGoalsBenefited,
    xg: withXg.length > 0 ? withXg.reduce((a, s) => a + s.xg!, 0) : null,
    missingXg: own.filter((s) => s.xg == null && !s.is_own_goal).length,
  };
}

/** 摘要整句的唯一出口(纯函数,测试直接断言它随筛选变化)。 */
export function buildShotMapSummary(args: {
  plotted: Shot[];
  plottableCount: number;
  shootout: number;
  homeName: string;
  awayName: string;
}): string {
  const { plotted, plottableCount, shootout, homeName, awayName } = args;
  const h = summarizeSide(plotted, true);
  const a = summarizeSide(plotted, false);
  const filtered = plotted.length !== plottableCount;
  const xgText = (s: ShotSideSummary) =>
    s.xg != null ? s.xg.toFixed(2) : s.n === 0 ? "0.00" : "—";
  const ownGoalTotal = h.ownGoalsBenefited + a.ownGoalsBenefited;
  const missingTotal = h.missingXg + a.missingXg;
  return (
    // "射门图 xG 合计"是逐次射门 xG 相加得出,与上方球队数据表的"官方统计
    // xG"是两个独立来源,数值可能有细微差异——分别命名,不用同一个"xG"混称。
    `射门图:${homeName}(攻向右)${h.n} 次射门、${h.goals} 球、射门图 xG 合计 ${xgText(h)};` +
    `${awayName}(攻向左)${a.n} 次射门、${a.goals} 球、射门图 xG 合计 ${xgText(a)}。` +
    // 形状图例语义必须与图上真实画法一致(§11.3:图例文字不能和图对不上)。
    `标记形状:足球图案为进球,实心圆为射正,空心圈为未射正(偏出、中框或被封堵);` +
    `标记大小与该次射门 xG 成正比,进球与小 xG 标记设有最小可读尺寸。` +
    (ownGoalTotal > 0
      ? `其中 ${ownGoalTotal} 球为乌龙球,计入受益方球数,不计入射门图 xG。`
      : "") +
    (missingTotal > 0
      ? `另有 ${missingTotal} 次射门缺少 xG 数据,未计入合计。`
      : "") +
    // 筛选后的数字是所选子集的合计,不是全场——必须说清楚口径,
    // 否则用户会把筛出来的 xG 当成全场 xG。
    (filtered
      ? `当前按筛选条件显示 ${plotted.length}/${plottableCount} 次射门,以上数字为所选范围的合计。`
      : "") +
    (shootout > 0 ? `另有 ${shootout} 次点球大战射门未计入本图与 xG 合计。` : "")
  );
}

/** 2026-08-24 抽出为可独立渲染冒烟测试的纯函数(CLAUDE.md §11.3)。
 * selected 非空时,如果它有可信轨迹终点(trajectoryEndpoint 非 null),
 * 追加一个 silent 的 custom 系列手绘轨迹线。
 *
 * 2026-08-25 标记改版:两条 scatter 合并成一条 custom 系列 "shots",按
 * markerKindFor 画三种形状。配色纪律(数字见 frontend/tests/
 * shot-map-contrast.test.ts 的逐条断言):
 *   - 空心圈环色 = 球队色(resolveMatchColors 已对 c.pitchBg 校验过 ≥3:1,
 *     品牌回退色实测 4.70~5.53),lineWidth 2 + RING_MIN_D 保证环画得出来;
 *   - 足球图案色 = c.pitchBg(球场"负空间"色)而**不是** c.ink——墨色
 *     图案压在球队色球体上四个组合全部 <3:1(浅 2.95/2.56、深 2.16/2.07);
 *   - 外圈描边 = c.ink(跟主题反向,vs 球场底 ≥11:1);
 *   - 所有 style 显式 opacity: 1——ECharts scatter 的 itemStyle.opacity
 *     **默认是 0.8**,此前从未显式设为 1,线上真实渲染的对比度比测试
 *     fixture 按 alpha=1 断言的低一档(teal 4.70→3.34)。custom 系列探针
 *     确认不加默认透明度,仍显式写 1,让 fixture 的 alpha=1 是事实不是假设。 */
export function buildOption(
  plotted: Shot[],
  homeName: string,
  awayName: string,
  c: ChartColors,
  selected?: Shot | null,
): EChartsOption {
  const ordered = orderShotsForRender(plotted);
  const markerSeries: EChartsOption["series"] = [
    {
      type: "custom",
      name: "shots", // resolveClickedShot 用它区分轨迹线系列
      cursor: "pointer",
      data: ordered.map((s) => ({ value: mirrorPoint(s.is_home, s.x!, s.y!), shot: s })),
      renderItem: (params, api) => {
        const s = ordered[params.dataIndex];
        if (!s) return null;
        const kind = markerKindFor(s);
        const [cx, cy] = api.coord([api.value(0) as number, api.value(1) as number]);
        const r = markerRadius(s, kind);
        const color = s.is_home ? c.teal : c.navy; // 已经过 resolveMatchColors

        if (kind === "off_target") {
          return {
            type: "circle",
            shape: { cx, cy, r },
            style: { fill: "none", stroke: color, lineWidth: 2, opacity: 1 },
          } as unknown as CustomSeriesRenderItemReturn;
        }
        if (kind === "on_target") {
          return {
            type: "circle",
            shape: { cx, cy, r },
            style: { fill: color, stroke: c.ink, lineWidth: 1, opacity: 1 },
          } as unknown as CustomSeriesRenderItemReturn;
        }
        return {
          type: "group",
          children: [
            {
              type: "circle",
              shape: { cx, cy, r },
              style: { fill: color, stroke: c.ink, lineWidth: 1.5, opacity: 1 },
            },
            ...ballPatchPolygons().map((pts) => ({
              type: "polygon",
              shape: { points: pts.map(([px, py]) => [cx + px * r, cy + py * r]) },
              style: { fill: c.pitchBg, opacity: 1 },
            })),
          ],
        } as unknown as CustomSeriesRenderItemReturn;
      },
    },
  ];

  // 轨迹线:只在选中射门有可信终点数据时才追加这个系列,没有可信数据就
  // 干脆不加(不是加一个空系列)——这正是"缺失终点数据就静默不画线"的
  // 落地方式。终点与起点用同一个 mirrorPoint 变换,保证方向一致。
  const endpoint = selected ? trajectoryEndpoint(selected) : null;
  const trajectorySeries: EChartsOption["series"] =
    selected && endpoint
      ? [
          {
            type: "custom",
            // silent:true 必须保留:轨迹线覆盖在标记上,不 silent 会抢走
            // 它压住的那些标记的点击事件(点击详情面板的唯一入口)。
            // (2026-08-25 前这里的理由写的是"防 tooltip.formatter 对无
            // .shot 数据点抛异常"——tooltip 已整块删除,那个理由不复存在,
            // 但 silent 本身不能跟着删。)
            silent: true,
            data: [
              [
                ...mirrorPoint(selected.is_home, selected.x!, selected.y!),
                ...mirrorPoint(selected.is_home, endpoint.x, endpoint.y),
              ],
            ],
            renderItem: (_params, api) => {
              const p1 = api.coord([api.value(0), api.value(1)]);
              const p2 = api.coord([api.value(2), api.value(3)]);
              const children: unknown[] = [
                {
                  type: "line",
                  shape: { x1: p1[0], y1: p1[1], x2: p2[0], y2: p2[1] },
                  style: { stroke: c.ink, lineWidth: 1.5 },
                },
              ];
              if (endpoint.blocked) {
                // 垂直短线必须在像素空间算角度——球场纵横比 105:68 不是
                // 1:1,数据空间里"垂直"的两个方向投影到像素后并不是真的
                // 90 度;偏移量同样用固定像素长度,不能用数据空间坐标差。
                const dx = p2[0] - p1[0];
                const dy = p2[1] - p1[1];
                const angle = Math.atan2(dy, dx) + Math.PI / 2;
                const half = 6;
                const ox = Math.cos(angle) * half;
                const oy = Math.sin(angle) * half;
                children.push({
                  type: "line",
                  shape: {
                    x1: p2[0] - ox,
                    y1: p2[1] - oy,
                    x2: p2[0] + ox,
                    y2: p2[1] + oy,
                  },
                  style: { stroke: c.ink, lineWidth: 1.5 },
                });
              }
              return { type: "group", children } as unknown as CustomSeriesRenderItemReturn;
            },
          },
        ]
      : [];

  // homeName/awayName 不再对应独立系列(合并成一条 custom),仅保留在函数
  // 签名里维持既有调用形状——图例由 DOM 层渲染,不走 ECharts legend。
  void homeName;
  void awayName;
  return {
    grid: { left: 0, right: 0, top: 0, bottom: 0 },
    xAxis: { type: "value", min: 0, max: PITCH_LEN, show: false },
    yAxis: { type: "value", min: 0, max: PITCH_WID, show: false },
    // 不配置 tooltip(2026-08-25 删除):悬停信息与点击详情面板是同一批
    // 字段两套渲染路径,且手机上 touch 会同时触发 hover 和 click,弹框和
    // 面板一起出现。可发现性由球场下方常驻提示行 + cursor:"pointer" 承担。
    series: [...markerSeries, ...trajectorySeries],
  };
}

export function filterShots(
  shots: Shot[],
  f: {
    side: SideFilter;
    outcome: OutcomeFilter;
    situations: string[];
    bodyPart: string | null;
    half: HalfFilter;
  },
): Shot[] {
  return shots.filter((s) => {
    if (f.side === "home" && !s.is_home) return false;
    if (f.side === "away" && s.is_home) return false;
    if (f.outcome === "goal" && s.outcome !== "Goal") return false;
    if (f.outcome === "on_target" && !isOnTarget(s)) return false;
    if (f.situations.length > 0 && !f.situations.includes(s.situation ?? "")) return false;
    if (f.bodyPart && s.shot_type !== f.bodyPart) return false;
    if (f.half === "first" && s.period !== "FirstHalf") return false;
    if (f.half === "second" && s.period !== "SecondHalf") return false;
    return true;
  });
}

export function ShotMapChart({
  shots,
  homeName,
  awayName,
  homeTeamColor,
  awayTeamColor,
  homeCrestUrl,
  awayCrestUrl,
  shirtNumberByPlayerId,
  mode = "interactive",
}: {
  shots: MatchReport["shots"];
  homeName: string;
  awayName: string;
  /** 2026-08-24:真实球队配色(FotMob 已做撞色规避的配对级结果);缺失或
   * 对比度不达标时组件内部回退品牌青绿/蓝,调用方不需要自己判空。 */
  homeTeamColor?: TeamColorPair | null;
  awayTeamColor?: TeamColorPair | null;
  /** 2026-08-24:点击射门后的详情面板要用——队徽与球衣号映射表。 */
  homeCrestUrl?: string | null;
  awayCrestUrl?: string | null;
  shirtNumberByPlayerId?: Record<string, string>;
  /** export 模式(Studio 卡片):隐藏筛选控件,截图里的按钮是死的。 */
  mode?: ChartMode;
}) {
  const isExport = mode === "export";
  const c = useChartColors();
  const wrapRef = useRef<HTMLDivElement>(null);
  const [chartHeight, setChartHeight] = useState<number | null>(null);
  const [visible, setVisible] = useState(false);
  const [side, setSide] = useState<SideFilter>("both");
  const [outcome, setOutcome] = useState<OutcomeFilter>("all");
  const [situations, setSituations] = useState<string[]>([]);
  const [bodyPart, setBodyPart] = useState<string | null>(null);
  const [half, setHalf] = useState<HalfFilter>("all");
  // 2026-08-24:点击射门画轨迹线 + 详情翻页面板联动的"当前选中射门"——
  // 只记最后一次点击/翻页选中的是哪个 shot 对象,真正参与渲染的永远是
  // resolveSelectedShot(plotted, selected) 的返回值(见该函数注释)。
  const [selected, setSelected] = useState<Shot | null>(null);

  // 首次可见才挂图(hidden tab 下容器 0×0);同时按宽度维持球场纵横比
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) setVisible(true);
    });
    io.observe(el);
    const ro = new ResizeObserver(() => {
      const w = el.clientWidth;
      if (w > 0) setChartHeight(Math.round((w * PITCH_WID) / PITCH_LEN));
    });
    ro.observe(el);
    return () => {
      io.disconnect();
      ro.disconnect();
    };
  }, []);

  const plottable = shots.filter(
    (s) => s.period !== "PenaltyShootout" && s.x != null && s.y != null,
  );
  const shootout = shots.length - plottable.length;
  // 该场是否已回填 is_blocked(2026-08-23 起才采集)。一场比赛要么整场
  // 回填过、要么完全没有(单场 ingest 是全量重新落库),用 every 而不是
  // some——避免半场有数据、半场没有时误报"精确"。
  const hasPreciseOnTarget =
    plottable.length > 0 && plottable.every((s) => s.is_blocked != null);
  // 导出模式忽略筛选状态(卡片是静态图,固定展示全场)
  const plotted = useMemo(
    () =>
      isExport
        ? plottable
        : filterShots(plottable, { side, outcome, situations, bodyPart, half }),
    [isExport, plottable, side, outcome, situations, bodyPart, half],
  );
  const filtered = plotted.length !== plottable.length;

  // 2026-08-24:真实球队配色对着射门图自己的真实背景(中性球场底 c.pitchBg,
  // 不是页面卡片背景)算对比度——缺失或不安全时回退品牌色,详见
  // components/charts/matchTeamColors.ts 模块注释。
  const resolved = resolveMatchColors(homeTeamColor, awayTeamColor, {
    isDark: c.isDark,
    backgroundHex: c.pitchBg,
    fallback: { home: c.teal, away: c.navy },
  });
  const effectiveColors: ChartColors = { ...c, teal: resolved.home, navy: resolved.away };

  // 2026-08-24:摘要整句抽成纯函数 buildShotMapSummary(乌龙球按受益方计球、
  // 缺失 xG 不再静默当 0),组件只负责调用——聚合逻辑的正确性由
  // frontend/tests/shot-map-chart.test.ts 直接断言,不再只活在渲染路径里。
  const summary = buildShotMapSummary({
    plotted,
    plottableCount: plottable.length,
    shootout,
    homeName,
    awayName,
  });

  const activeSelected = resolveSelectedShot(plotted, selected);
  const option = buildOption(plotted, homeName, awayName, effectiveColors, activeSelected);

  // Esc 关闭详情面板(2026-08-25,"无法清除选中"修复的键盘路径;鼠标路径
  // 是面板右上角的关闭按钮)。只在真的有选中时挂监听。
  useEffect(() => {
    if (isExport || !activeSelected) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelected(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isExport, activeSelected]);

  if (plottable.length === 0) {
    return <p className={styles.empty}>该场比赛暂无射门位置数据。</p>;
  }

  const toggleSituation = (key: string) =>
    setSituations((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key],
    );
  const resetAll = () => {
    setSide("both");
    setOutcome("all");
    setSituations([]);
    setBodyPart(null);
    setHalf("all");
  };

  // 只在非导出模式绑定——导出模式(Studio PNG)不接选中态/轨迹线/详情面板,
  // 避免热链头像跨源污染 html-to-image 的 canvas(见 PlayerAvatar.tsx 头部
  // 注释),筛选控件本来也在导出模式隐藏,同一个 isExport 判断复用。
  const handleChartClick = (params: unknown) => {
    // 与 buildOption 使用同一个 orderShotsForRender 排序,dataIndex 才对得上号
    // (见 resolveClickedShot 注释)。
    const clicked = resolveClickedShot(params, orderShotsForRender(plotted));
    if (clicked) setSelected(clicked);
  };
  const activeIndex = activeSelected ? plotted.indexOf(activeSelected) : -1;
  const goToOffset = (offset: number) => {
    if (plotted.length === 0) return;
    const base = activeIndex >= 0 ? activeIndex : 0;
    const next = (base + offset + plotted.length) % plotted.length;
    setSelected(plotted[next]);
  };

  return (
    <div ref={wrapRef} className={styles.wrap}>
      {!isExport && (
        <div className={styles.filters} aria-label="射门筛选">
          <div className={styles.filterRow}>
            <div className={styles.segmented}>
              {(
                [
                  ["both", "双方"],
                  ["home", homeName],
                  ["away", awayName],
                ] as const
              ).map(([k, label]) => (
                <button
                  key={k}
                  type="button"
                  className={side === k ? styles.active : undefined}
                  aria-pressed={side === k}
                  onClick={() => setSide(k)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className={styles.segmented}>
              {(
                // AttemptSaved 混了门将扑救与后卫封堵,单次射门层面此前无法
                // 精确区分,标注为"射正(含被封堵)"避免和球队官方射正数字
                // 对不上;2026-08-23 起已回填的场次(hasPreciseOnTarget)
                // 能用 is_blocked 精确排除被封堵的球,标签相应改回"射正"。
                [
                  ["all", "全部"],
                  ["on_target", hasPreciseOnTarget ? "射正" : "射正(含被封堵)"],
                  ["goal", "进球"],
                ] as const
              ).map(([k, label]) => (
                <button
                  key={k}
                  type="button"
                  className={outcome === k ? styles.active : undefined}
                  aria-pressed={outcome === k}
                  onClick={() => setOutcome(k)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className={styles.segmented}>
              {(
                [
                  ["all", "全场"],
                  ["first", "上半场"],
                  ["second", "下半场"],
                ] as const
              ).map(([k, label]) => (
                <button
                  key={k}
                  type="button"
                  className={half === k ? styles.active : undefined}
                  aria-pressed={half === k}
                  onClick={() => setHalf(k)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div className={styles.chips}>
            {SITUATION_CHIPS.map((key) => (
              <button
                key={key}
                type="button"
                className={situations.includes(key) ? styles.chipOn : styles.chip}
                aria-pressed={situations.includes(key)}
                onClick={() => toggleSituation(key)}
              >
                {SHOT_SITUATION_ZH[key] ?? key}
              </button>
            ))}
            {BODY_PARTS.map((key) => (
              <button
                key={key}
                type="button"
                className={bodyPart === key ? styles.chipOn : styles.chip}
                aria-pressed={bodyPart === key}
                onClick={() => setBodyPart(bodyPart === key ? null : key)}
              >
                {SHOT_TYPE_ZH[key] ?? key}
              </button>
            ))}
            {filtered && (
              <button type="button" className={styles.reset} onClick={resetAll}>
                重置
              </button>
            )}
          </div>
        </div>
      )}
      <div className={styles.legend}>
        <span className={styles.legendHome} style={{ color: resolved.home }}>
          {homeName}(攻向右)
        </span>
        <span className={styles.legendAway} style={{ color: resolved.away }}>
          {awayName}(攻向左)
        </span>
      </div>
      {/* 形状图例(2026-08-25):颜色语义由上面的队名图例承担,这里只讲
          形状,统一用中性 --ink-2 画样例(足球图案的负空间用 --surface,
          与图上"球体色 + 负空间图案"的结构一致);文字 12px 下限(§11.2)。
          语义同一句话也在下方文字摘要里,图例不 aria-hidden 图标即可。 */}
      <div className={styles.shapeLegend}>
        <span className={styles.shapeItem}>
          <svg viewBox="-1.2 -1.2 2.4 2.4" className={styles.shapeIcon} aria-hidden>
            <circle cx={0} cy={0} r={1} fill="var(--ink-2)" />
            {ballPatchPolygons().map((pts, i) => (
              <polygon
                key={i}
                points={pts.map(([px, py]) => `${px},${py}`).join(" ")}
                fill="var(--surface)"
              />
            ))}
          </svg>
          进球
        </span>
        <span className={styles.shapeItem}>
          <svg viewBox="-1.2 -1.2 2.4 2.4" className={styles.shapeIcon} aria-hidden>
            <circle cx={0} cy={0} r={1} fill="var(--ink-2)" />
          </svg>
          射正
        </span>
        <span className={styles.shapeItem}>
          <svg viewBox="-1.2 -1.2 2.4 2.4" className={styles.shapeIcon} aria-hidden>
            <circle cx={0} cy={0} r={0.85} fill="none" stroke="var(--ink-2)" strokeWidth={0.3} />
          </svg>
          未射正(偏出/中框/被封堵)
        </span>
      </div>
      <div className={styles.pitchWrap}>
        <FootballPitchBackground variant="neutral" />
        {plotted.length === 0 && (
          <p className={styles.noMatch}>当前筛选条件下没有射门。</p>
        )}
        {visible && chartHeight != null && (
          <EChart
            option={option}
            height={chartHeight}
            ariaSummary={summary}
            className={styles.chart}
            mode={mode}
            showSummary={false}
            onEvents={isExport ? undefined : { click: handleChartClick }}
          />
        )}
      </div>
      {/* 常驻一行小字(2026-08-25):同时承担 (a) 详情面板未选中时的空态
          说明;(b) 删掉 hover tooltip 后唯一的可点击性提示——没有它,
          点击交互完全不可发现。 */}
      {!isExport && <p className={styles.hint}>点击任意射门点查看详情</p>}
      {/* 可见文字摘要固定在球场下方(EChart 内置的那份在球场层里隐藏,
          避免把装饰线的百分比定位基准拉歪;aria-label 仍在图表上) */}
      <p className="chart-summary">{summary}</p>
      {!isExport && (
        /* 面板出现/消失走 grid 行高过渡(不做 JS 高度测量),未选中时
           不占任何高度;面板在球场下方,展开只推动其下的小节,不推球场。 */
        <div className={styles.panelSlot} data-open={activeSelected != null}>
          <div>
            <ShotDetailPanel
              shot={activeSelected}
              homeName={homeName}
              awayName={awayName}
              homeCrestUrl={homeCrestUrl}
              awayCrestUrl={awayCrestUrl}
              homeColor={resolved.home}
              awayColor={resolved.away}
              shirtNumberByPlayerId={shirtNumberByPlayerId}
              onPrev={() => goToOffset(-1)}
              onNext={() => goToOffset(1)}
              onClose={() => setSelected(null)}
              hasPrev={plotted.length > 1}
              hasNext={plotted.length > 1}
              position={activeIndex >= 0 ? activeIndex + 1 : null}
              total={plotted.length}
            />
          </div>
        </div>
      )}
    </div>
  );
}
