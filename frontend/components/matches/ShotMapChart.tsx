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
import type { ChartColors } from "@/components/charts/useChartColors";
import { useChartColors } from "@/components/charts/useChartColors";
import { resolveMatchColors, type TeamColorPair } from "@/components/charts/matchTeamColors";
import type { MatchReportResponse } from "@/lib/api-v1";
import { SHOT_OUTCOME_ZH, SHOT_SITUATION_ZH, SHOT_TYPE_ZH } from "@/components/matches/zh";
import { FootballPitchBackground } from "./FootballPitchBackground";
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
function outcomeLabelFor(s: Shot): string {
  if (s.outcome === "AttemptSaved" && s.is_blocked != null) {
    return s.is_blocked ? "被封堵" : "被扑出";
  }
  return SHOT_OUTCOME_ZH[s.outcome ?? ""] ?? s.outcome ?? "";
}

function shotTooltip(s: Shot): string {
  const parts = [
    `${s.player_name ?? s.player_id}${s.minute != null ? ` ${s.minute}'` : ""}`,
    `${outcomeLabelFor(s)} · xG ${s.xg?.toFixed(3) ?? "—"}`,
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

/**
 * 纯筛选:只做"少显示一些",绝不改动任何一次射门的数值。
 * 统计数字随筛选结果重算 —— 筛选后的 xG 合计就是所选子集的合计,
 * 不是全场合计,摘要里会说明当前口径。
 */
const toPoint = (s: Shot) =>
  s.is_home
    ? { value: [s.x!, s.y!], shot: s }
    : { value: [PITCH_LEN - s.x!, PITCH_WID - s.y!], shot: s };

/** 2026-08-24 抽出为可独立渲染冒烟测试的纯函数(CLAUDE.md §11.3)。 */
export function buildOption(
  plotted: Shot[],
  homeName: string,
  awayName: string,
  c: ChartColors,
): EChartsOption {
  const seriesOf = (isHome: boolean, color: string) =>
    plotted
      .filter((s) => s.is_home === isHome)
      .map((s) => ({
        ...toPoint(s),
        symbolSize: symbolSize(s.xg),
        // 2026-08-24:球场底改中性色(FootballPitchBackground variant="neutral",
        // 见该文件顶部 FotMob 实测说明)后,标记不再需要靠半透明去"融进"草坪——
        // 那正是上次改配色时非进球点变隐形的原因(合成后对比度只有 1.09~1.17:1)。
        // 现在两种结果都不透明 + 描边,只用描边粗细区分进球:
        //   进球   = 更粗的描边(强调,与 FotMob layer-list 单独一张 goal
        //            drawable 同一思路——进球必须一眼跳出来);
        //   非进球 = 细描边,与中性球场底天然可辨。
        // 描边颜色用 c.ink(--ink)而不是硬编码白色——白色描边在浅色中性球场
        // (#F8FAFA)上实测只有 1.05:1(近乎白压白,等于没描);--ink 浅色模式深
        // /深色模式亮,永远跟当前主题的球场底色反向,两个主题都 ≥11:1(见
        // frontend/tests/shot-map-contrast.test.ts)。
        itemStyle:
          s.outcome === "Goal"
            ? { color, borderColor: c.ink, borderWidth: 2.5 }
            : { color, borderColor: c.ink, borderWidth: 1 },
      }));

  return {
    grid: { left: 0, right: 0, top: 0, bottom: 0 },
    xAxis: { type: "value", min: 0, max: PITCH_LEN, show: false },
    yAxis: { type: "value", min: 0, max: PITCH_WID, show: false },
    tooltip: {
      trigger: "item",
      formatter: (p) =>
        shotTooltip((p as unknown as { data: { shot: Shot } }).data.shot),
    },
    series: [
      { name: homeName, type: "scatter", data: seriesOf(true, c.teal) },
      { name: awayName, type: "scatter", data: seriesOf(false, c.navy) },
    ],
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
  mode = "interactive",
}: {
  shots: MatchReport["shots"];
  homeName: string;
  awayName: string;
  /** 2026-08-24:真实球队配色(FotMob 已做撞色规避的配对级结果);缺失或
   * 对比度不达标时组件内部回退品牌青绿/蓝,调用方不需要自己判空。 */
  homeTeamColor?: TeamColorPair | null;
  awayTeamColor?: TeamColorPair | null;
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
    `圆点大小与该次射门 xG 成正比,描边更粗为进球。` +
    // 筛选后的数字是所选子集的合计,不是全场 —— 必须说清楚口径,
    // 否则用户会把筛出来的 xG 当成全场 xG。
    (filtered
      ? `当前按筛选条件显示 ${plotted.length}/${plottable.length} 次射门,以上数字为所选范围的合计。`
      : "") +
    (shootout > 0 ? `另有 ${shootout} 次点球大战射门未计入本图与 xG 合计。` : "");

  const option = buildOption(plotted, homeName, awayName, effectiveColors);

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
          />
        )}
      </div>
      {/* 可见文字摘要固定在球场下方(EChart 内置的那份在球场层里隐藏,
          避免把装饰线的百分比定位基准拉歪;aria-label 仍在图表上) */}
      <p className="chart-summary">{summary}</p>
    </div>
  );
}
