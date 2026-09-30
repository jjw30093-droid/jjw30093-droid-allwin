"use client";

/**
 * xG 累积对抗曲线("xG race")—— 两条阶梯累积折线,进球分钟打标记。
 *
 * 视觉语法参考 ggfootball 的 xG timeline:两条线一路爬升,谁爬得高谁创造的
 * 机会质量高;比分与曲线背离时就是"踢得好但输了"的标准叙事,是短视频里
 * 最好用的一张图。
 *
 * 用阶梯线(step:'end')而不是平滑折线:xG 是在射门那一刻离散跳增的,
 * 两次射门之间并没有连续增长,画平滑曲线是错误的图形语义。
 *
 * 数据:fact_shotmap,与 ThreatTimeline 同源,后端零改动。
 * 点球大战排除(不属于比赛 xG)。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type { EChartsOption } from "echarts";
import { EChart } from "@/components/EChart";
import type { ChartMode } from "@/components/charts/chartMode";
import { tokensFor } from "@/components/charts/chartMode";
import type { ChartColors } from "@/components/charts/useChartColors";
import type { TeamBrandColor, TeamColorPair } from "@/components/charts/matchTeamColors";
import { useMatchColors } from "@/components/charts/useMatchColors";
import type { MatchReportResponse } from "@/lib/api-v1";

type MatchReport = Extract<MatchReportResponse, { available: true }>;
type Shot = MatchReport["shots"][number];

/** 可选携带补时分钟数(minute_added);只有启用补时横轴(stoppage)时才会用到。 */
export type XgRaceShot = Shot & { minute_added?: number | null };

/** 上 / 下半场补时长度(分钟)。传入时横轴展开补时:上半场补时排在 45' 之后、
 * 下半场之前,下半场整体右移上半场补时的长度,终点为 90+下半场补时。 */
export type StoppageAxis = { firstHalf: number; secondHalf: number };

type Point = { minute: number; total: number; goal: XgRaceShot | null };

/** 启用补时横轴时一次射门在横轴上的位置。 */
export function stoppageAxisPosition(
  s: Pick<XgRaceShot, "minute" | "period" | "minute_added">,
  st: StoppageAxis,
): number {
  const minute = s.minute ?? 0;
  const added = Math.max(0, s.minute_added ?? 0);
  const secondHalf = s.period === "SecondHalf" || (s.period == null && minute > 45);
  if (!secondHalf) return Math.min(minute, 45) + (minute >= 45 ? Math.min(added, st.firstHalf) : 0);
  return Math.min(minute, 90) + st.firstHalf + (minute >= 90 ? Math.min(added, st.secondHalf) : 0);
}

/** 补时横轴位置 → 比赛时钟文字(45+2'、90+5' 这类)。 */
export function stoppageAxisLabel(v: number, st: StoppageAxis): string {
  const m = Math.round(v);
  if (m <= 45) return `${m}'`;
  if (m <= 45 + st.firstHalf) return `45+${m - 45}'`;
  if (m <= 90 + st.firstHalf) return `${m - st.firstHalf}'`;
  return `90+${m - 90 - st.firstHalf}'`;
}

/** 逐分钟累积:返回从 0 分钟起的阶梯点序列。传入 stoppage 时横轴位置按补时展开。 */
export function cumulativeSeries(shots: XgRaceShot[], isHome: boolean, stoppage?: StoppageAxis): Point[] {
  const pos = (s: XgRaceShot) => (stoppage ? stoppageAxisPosition(s, stoppage) : (s.minute ?? 0));
  const mine = shots
    .filter(
      (s) =>
        s.period !== "PenaltyShootout" && s.minute != null && s.is_home === isHome,
    )
    .sort((a, b) => pos(a) - pos(b));
  const out: Point[] = [{ minute: 0, total: 0, goal: null }];
  let total = 0;
  for (const s of mine) {
    total += s.xg ?? 0;
    out.push({
      minute: pos(s),
      total,
      goal: s.outcome === "Goal" ? s : null,
    });
  }
  return out;
}

/** 补时横轴的刻度位置:0/15/30/45'、下半场 60/75/90'(右移上半场补时)、终点 90+X'。 */
export function stoppageTicks(st: StoppageAxis): number[] {
  const ticks = [0, 15, 30, 45, 60 + st.firstHalf, 75 + st.firstHalf, 90 + st.firstHalf];
  if (st.secondHalf > 0) ticks.push(90 + st.firstHalf + st.secondHalf);
  return ticks;
}

/** 导出供渲染冒烟测试直接调用(frontend/tests/chart-render-smoke.test.ts,
 * 见 CLAUDE.md §11.3)。 */
export function buildOption(
  home: Point[],
  away: Point[],
  homeName: string,
  awayName: string,
  endMinute: number,
  mode: ChartMode,
  c: ChartColors,
  stoppage?: StoppageAxis,
): EChartsOption {
  const t = tokensFor(mode);
  // 曲线画到终场:补一个末端点,否则线在最后一次射门处就断了
  const extend = (pts: Point[]) =>
    pts.length && pts[pts.length - 1].minute < endMinute
      ? [...pts, { ...pts[pts.length - 1], minute: endMinute, goal: null }]
      : pts;
  const h = extend(home);
  const a = extend(away);

  // markPoint 用 coord 定位(value 只接受标量,数组会被当成"值"而不是坐标)
  const goalMarks = (pts: Point[], color: string) =>
    pts
      .filter((p) => p.goal)
      .map((p) => ({
        coord: [p.minute, p.total] as [number, number],
        name: p.goal?.player_name ?? "",
        itemStyle: { color },
      }));

  return {
    grid: {
      left: mode === "export" ? 96 : 46,
      right: mode === "export" ? 48 : 18,
      top: mode === "export" ? 70 : 34,
      bottom: mode === "export" ? 66 : 28,
    },
    legend: {
      data: [homeName, awayName],
      textStyle: { color: c.ink2, fontSize: t.legendFont },
      top: 0,
      itemHeight: Math.round(t.legendFont * 0.8),
      itemWidth: Math.round(t.legendFont * 1.6),
    },
    tooltip:
      mode === "export"
        ? undefined
        : {
            trigger: "axis",
            // 窄屏上跟随光标的浮层会溢出容器被裁,限制在图内
            confine: true,
            formatter: (params: unknown) => {
              const rows = params as Array<{
                seriesName: string;
                value: [number, number];
              }>;
              if (!rows?.length) return "";
              const min = rows[0].value[0];
              const head = stoppage ? `${stoppageAxisLabel(min, stoppage)}` : `第 ${min} 分钟`;
              return `${head}<br/>${rows
                .map((r) => `${r.seriesName} 累积 xG ${r.value[1].toFixed(2)}`)
                .join("<br/>")}`;
            },
          },
    xAxis: stoppage
      ? {
          type: "value",
          min: 0,
          max: endMinute,
          axisTick: { customValues: stoppageTicks(stoppage) },
          axisLabel: {
            color: c.ink2,
            fontSize: t.axisFont,
            customValues: stoppageTicks(stoppage),
            hideOverlap: true,
            formatter: (v: number) => stoppageAxisLabel(v, stoppage),
          },
          splitLine: { show: false },
        }
      : {
          type: "value",
          min: 0,
          max: endMinute,
          interval: 15,
          axisLabel: { color: c.ink2, fontSize: t.axisFont, formatter: (v: number) => `${v}'` },
          splitLine: { show: false },
        },
    yAxis: {
      type: "value",
      axisLabel: {
        color: c.ink2,
        fontSize: t.axisFont,
        formatter: (v: number) => v.toFixed(1),
      },
      splitLine: { lineStyle: { opacity: 0.15 } },
    },
    series: [
      {
        name: homeName,
        type: "line",
        step: "end",
        showSymbol: false,
        data: h.map((p) => [p.minute, p.total]),
        color: c.teal,
        lineStyle: { width: t.lineWidth },
        areaStyle: { opacity: 0.12 },
        // 进球点与标签用本队曲线的颜色(此前两队都用 c.win,客队进球会显示成主队的青绿)
        markPoint: {
          symbol: "circle",
          symbolSize: t.symbolSize + 4,
          data: goalMarks(home, c.teal),
          label: {
            show: true,
            position: "top",
            fontSize: Math.max(12, Math.round(t.axisFont * 0.95)),
            color: c.teal,
            formatter: ({ name }: { name: string }) => name,
          },
        },
        ...(stoppage
          ? {
              markArea: {
                silent: true,
                itemStyle: { color: c.grey, opacity: 0.18 },
                data: [
                  [{ xAxis: 45 }, { xAxis: 45 + stoppage.firstHalf }],
                  [{ xAxis: 90 + stoppage.firstHalf }, { xAxis: endMinute }],
                ],
              },
            }
          : {}),
      },
      {
        name: awayName,
        type: "line",
        step: "end",
        showSymbol: false,
        data: a.map((p) => [p.minute, p.total]),
        color: c.navy,
        lineStyle: { width: t.lineWidth },
        areaStyle: { opacity: 0.08 },
        markPoint: {
          symbol: "circle",
          symbolSize: t.symbolSize + 4,
          data: goalMarks(away, c.navy),
          label: {
            show: true,
            position: "bottom",
            fontSize: Math.max(12, Math.round(t.axisFont * 0.95)),
            color: c.navy,
            formatter: ({ name }: { name: string }) => name,
          },
        },
      },
    ],
  };
}

export function XgRaceChart({
  shots,
  homeName,
  awayName,
  homeTeamColor,
  awayTeamColor,
  homeTeamBrandColor,
  awayTeamBrandColor,
  homeScore,
  awayScore,
  mode = "interactive",
  height,
  stoppage,
  showSummary,
}: {
  shots: XgRaceShot[];
  homeName: string;
  awayName: string;
  /** 2026-08-24:真实球队配色,缺失或对比度不达标时回退品牌青绿/蓝。 */
  homeTeamColor?: TeamColorPair | null;
  awayTeamColor?: TeamColorPair | null;
  /** 该队近期代表色:本场配色缺失或校验不过时的第二级(见 charts/matchTeamColors.ts) */
  homeTeamBrandColor?: TeamBrandColor | null;
  awayTeamBrandColor?: TeamBrandColor | null;
  homeScore?: number | null;
  awayScore?: number | null;
  mode?: ChartMode;
  height?: number;
  /** 可选:展开补时横轴(默认不展开,行为与此前一致)。 */
  stoppage?: StoppageAxis;
  /** 可选:是否在图下显示文字摘要(默认同此前:export 模式不显示,其余显示)。
   *  模拟器结果页自己在图上方给出一行累计 xG,关掉这段长解释;读屏摘要照常保留。 */
  showSummary?: boolean;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(mode === "export");
  const { effectiveColors } = useMatchColors(
    { homeTeamColor, awayTeamColor, homeTeamBrandColor, awayTeamBrandColor },
    "surface",
  );

  useEffect(() => {
    if (mode === "export") return;
    const el = wrapRef.current;
    if (!el) return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) setVisible(true);
    });
    io.observe(el);
    return () => io.disconnect();
  }, [mode]);

  const home = useMemo(() => cumulativeSeries(shots, true, stoppage), [shots, stoppage]);
  const away = useMemo(() => cumulativeSeries(shots, false, stoppage), [shots, stoppage]);
  const endMinute = useMemo(
    () =>
      stoppage
        ? 90 + stoppage.firstHalf + stoppage.secondHalf
        : Math.max(
            90,
            ...shots
              .filter((s) => s.period !== "PenaltyShootout")
              .map((s) => s.minute ?? 0),
          ),
    [shots, stoppage],
  );

  const hTotal = home.length ? home[home.length - 1].total : 0;
  const aTotal = away.length ? away[away.length - 1].total : 0;

  const ariaSummary = useMemo(() => {
    if (home.length <= 1 && away.length <= 1) return "本场没有可用的射门数据。";
    const scoreLine =
      homeScore != null && awayScore != null
        ? `实际比分 ${homeScore}–${awayScore};`
        : "";
    // 把 xG 从术语翻译成"人话":它衡量的是机会质量,不是必然结果
    return (
      `xG 累积对抗:${homeName} ${hTotal.toFixed(2)},${awayName} ${aTotal.toFixed(2)}。` +
      `${scoreLine}xG 表示这些射门机会平均能打进多少球,数值高的一方创造的机会质量更好,` +
      `但不等于一定赢 —— 曲线与比分背离正是本图要显示的东西。`
    );
  }, [home.length, away.length, hTotal, aTotal, homeName, awayName, homeScore, awayScore]);

  const option = useMemo(
    () => buildOption(home, away, homeName, awayName, endMinute, mode, effectiveColors, stoppage),
    [home, away, homeName, awayName, endMinute, mode, effectiveColors, stoppage],
  );

  if (home.length <= 1 && away.length <= 1) {
    return null;
  }

  return (
    <div ref={wrapRef}>
      {visible ? (
        <EChart
          option={option}
          height={height ?? (mode === "export" ? 420 : 260)}
          ariaSummary={ariaSummary}
          mode={mode}
          showSummary={showSummary ?? mode !== "export"}
        />
      ) : (
        <div style={{ height: height ?? 260 }} aria-hidden />
      )}
    </div>
  );
}
