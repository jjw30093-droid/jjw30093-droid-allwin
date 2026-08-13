"use client";

/**
 * 单场射门图(ECharts scatter,唯一图表库,复用底层 EChart 封装;不进
 * SpecCharts——那是 analysis_bundle.chart_specs 的封闭类型联合)。
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
import type { EChartsOption } from "echarts";
import { EChart } from "@/components/EChart";
import type { ChartMode } from "@/components/charts/chartMode";
import type { MatchReportResponse } from "@/lib/api-v1";
import { SHOT_OUTCOME_ZH, SHOT_SITUATION_ZH, SHOT_TYPE_ZH } from "@/components/matches/zh";
import styles from "./ShotMapChart.module.css";

type MatchReport = Extract<MatchReportResponse, { available: true }>;
type Shot = MatchReport["shots"][number];

const PITCH_LEN = 105;
const PITCH_WID = 68;
// 与 SpecCharts 同一套图表配色惯例(主=金,客=中性)
const GOLD = "#d49e33";
const INK2 = "#a79c87";

function symbolSize(xg: number | null | undefined): number {
  return 7 + Math.min(1, xg ?? 0) * 22;
}

function shotTooltip(s: Shot): string {
  const parts = [
    `${s.player_name ?? s.player_id}${s.minute != null ? ` ${s.minute}'` : ""}`,
    `${SHOT_OUTCOME_ZH[s.outcome ?? ""] ?? s.outcome ?? ""} · xG ${s.xg?.toFixed(3) ?? "—"}`,
  ];
  const detail = [
    s.situation ? SHOT_SITUATION_ZH[s.situation] ?? s.situation : null,
    s.shot_type ? SHOT_TYPE_ZH[s.shot_type] ?? s.shot_type : null,
  ].filter(Boolean);
  if (detail.length) parts.push(detail.join(" · "));
  return parts.join("<br/>");
}

type SideFilter = "both" | "home" | "away";
type OutcomeFilter = "all" | "on_target" | "goal";
type HalfFilter = "all" | "first" | "second";

/** 图上真正用到的射门情境(库里 8 种,这里只列竞彩用户会主动筛的几种)。 */
const SITUATION_CHIPS = ["FromCorner", "SetPiece", "FreeKick", "FastBreak", "Penalty"];
const BODY_PARTS = ["LeftFoot", "RightFoot", "Header"];

/** 射正 = 进球 + 被扑出(中框/偏出不算射正,与球队统计口径一致)。 */
function isOnTarget(s: Shot): boolean {
  return s.outcome === "Goal" || s.outcome === "AttemptSaved";
}

/**
 * 纯筛选:只做"少显示一些",绝不改动任何一次射门的数值。
 * 统计数字随筛选结果重算 —— 筛选后的 xG 合计就是所选子集的合计,
 * 不是全场合计,摘要里会说明当前口径。
 */
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
  mode = "interactive",
}: {
  shots: MatchReport["shots"];
  homeName: string;
  awayName: string;
  /** export 模式(Studio 卡片):隐藏筛选控件,截图里的按钮是死的。 */
  mode?: ChartMode;
}) {
  const isExport = mode === "export";
  const wrapRef = useRef<HTMLDivElement>(null);
  const [chartHeight, setChartHeight] = useState<number | null>(null);
  const [visible, setVisible] = useState(false);
  const [side, setSide] = useState<SideFilter>("both");
  const [outcome, setOutcome] = useState<OutcomeFilter>("all");
  const [situations, setSituations] = useState<string[]>([]);
  const [bodyPart, setBodyPart] = useState<string | null>(null);
  const [half, setHalf] = useState<HalfFilter>("all");

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
  // 导出模式忽略筛选状态(卡片是静态图,固定展示全场)
  const plotted = useMemo(
    () =>
      isExport
        ? plottable
        : filterShots(plottable, { side, outcome, situations, bodyPart, half }),
    [isExport, plottable, side, outcome, situations, bodyPart, half],
  );
  const filtered = plotted.length !== plottable.length;

  const toPoint = (s: Shot) =>
    s.is_home
      ? { value: [s.x!, s.y!], shot: s }
      : { value: [PITCH_LEN - s.x!, PITCH_WID - s.y!], shot: s };

  const seriesOf = (isHome: boolean, color: string) =>
    plotted
      .filter((s) => s.is_home === isHome)
      .map((s) => ({
        ...toPoint(s),
        symbolSize: symbolSize(s.xg),
        itemStyle:
          s.outcome === "Goal"
            ? { color, borderColor: "#ffffff", borderWidth: 2 }
            : { color, opacity: 0.55 },
      }));

  const sum = (isHome: boolean, f: (s: Shot) => number) =>
    plotted.filter((s) => s.is_home === isHome).reduce((a, s) => a + f(s), 0);
  const stats = (isHome: boolean) => ({
    n: plotted.filter((s) => s.is_home === isHome).length,
    goals: plotted.filter((s) => s.is_home === isHome && s.outcome === "Goal").length,
    xg: sum(isHome, (s) => s.xg ?? 0),
  });
  const h = stats(true);
  const a = stats(false);
  const summary =
    // "射门图 xG 合计"是逐次射门 xG 相加得出,与上方球队数据表的"官方统计
    // xG"是两个独立来源(前者本组件按 shots[] 求和;后者取自 FotMob 团队
    // 统计接口),数值可能有细微差异——分别命名,不用同一个"xG"混称。
    `射门图:${homeName}(攻向右)${h.n} 次射门、${h.goals} 球、射门图 xG 合计 ${h.xg.toFixed(2)};` +
    `${awayName}(攻向左)${a.n} 次射门、${a.goals} 球、射门图 xG 合计 ${a.xg.toFixed(2)}。` +
    `圆点大小与该次射门 xG 成正比,实心带白边为进球。` +
    // 筛选后的数字是所选子集的合计,不是全场 —— 必须说清楚口径,
    // 否则用户会把筛出来的 xG 当成全场 xG。
    (filtered
      ? `当前按筛选条件显示 ${plotted.length}/${plottable.length} 次射门,以上数字为所选范围的合计。`
      : "") +
    (shootout > 0 ? `另有 ${shootout} 次点球大战射门未计入本图与 xG 合计。` : "");

  const option: EChartsOption = {
    grid: { left: 0, right: 0, top: 0, bottom: 0 },
    xAxis: { type: "value", min: 0, max: PITCH_LEN, show: false },
    yAxis: { type: "value", min: 0, max: PITCH_WID, show: false },
    tooltip: {
      trigger: "item",
      formatter: (p) =>
        shotTooltip((p as unknown as { data: { shot: Shot } }).data.shot),
    },
    series: [
      { name: homeName, type: "scatter", data: seriesOf(true, GOLD) },
      { name: awayName, type: "scatter", data: seriesOf(false, INK2) },
    ],
  };

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
                // AttemptSaved 混了门将扑救与后卫封堵,单次射门层面无法
                // 精确区分——标注写清楚,避免和球队官方射正数字对不上。
                [
                  ["all", "全部"],
                  ["on_target", "射正(含被封堵)"],
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
        <span className={styles.legendHome}>{homeName}(攻向右)</span>
        <span className={styles.legendAway}>{awayName}(攻向左)</span>
      </div>
      <div className={styles.pitchWrap}>
        <div className={styles.halfLine} aria-hidden />
        <div className={styles.centerCircle} aria-hidden />
        <div className={`${styles.box} ${styles.boxLeft}`} aria-hidden />
        <div className={`${styles.box} ${styles.boxRight}`} aria-hidden />
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
          />
        )}
      </div>
      {/* 可见文字摘要固定在球场下方(EChart 内置的那份在球场层里隐藏,
          避免把装饰线的百分比定位基准拉歪;aria-label 仍在图表上) */}
      <p className="chart-summary">{summary}</p>
    </div>
  );
}
