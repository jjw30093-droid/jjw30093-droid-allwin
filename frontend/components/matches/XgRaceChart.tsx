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
import { useChartColors, type ChartColors } from "@/components/charts/useChartColors";
import { resolveMatchColors, type TeamColorPair } from "@/components/charts/matchTeamColors";
import type { MatchReportResponse } from "@/lib/api-v1";

type MatchReport = Extract<MatchReportResponse, { available: true }>;
type Shot = MatchReport["shots"][number];

type Point = { minute: number; total: number; goal: Shot | null };

/** 逐分钟累积:返回从 0 分钟起的阶梯点序列。 */
export function cumulativeSeries(shots: Shot[], isHome: boolean): Point[] {
  const mine = shots
    .filter(
      (s) =>
        s.period !== "PenaltyShootout" && s.minute != null && s.is_home === isHome,
    )
    .sort((a, b) => (a.minute ?? 0) - (b.minute ?? 0));
  const out: Point[] = [{ minute: 0, total: 0, goal: null }];
  let total = 0;
  for (const s of mine) {
    total += s.xg ?? 0;
    out.push({
      minute: s.minute ?? 0,
      total,
      goal: s.outcome === "Goal" ? s : null,
    });
  }
  return out;
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
              return `第 ${min} 分钟<br/>${rows
                .map((r) => `${r.seriesName} 累积 xG ${r.value[1].toFixed(2)}`)
                .join("<br/>")}`;
            },
          },
    xAxis: {
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
        markPoint: {
          symbol: "circle",
          symbolSize: t.symbolSize + 4,
          data: goalMarks(home, c.win),
          label: {
            show: true,
            position: "top",
            fontSize: Math.max(12, Math.round(t.axisFont * 0.95)),
            color: c.win,
            formatter: ({ name }: { name: string }) => name,
          },
        },
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
          data: goalMarks(away, c.win),
          label: {
            show: true,
            position: "bottom",
            fontSize: Math.max(12, Math.round(t.axisFont * 0.95)),
            color: c.win,
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
  homeScore,
  awayScore,
  mode = "interactive",
  height,
}: {
  shots: MatchReport["shots"];
  homeName: string;
  awayName: string;
  /** 2026-08-24:真实球队配色,缺失或对比度不达标时回退品牌青绿/蓝。 */
  homeTeamColor?: TeamColorPair | null;
  awayTeamColor?: TeamColorPair | null;
  homeScore?: number | null;
  awayScore?: number | null;
  mode?: ChartMode;
  height?: number;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(mode === "export");
  const c = useChartColors();
  const resolved = useMemo(
    () =>
      resolveMatchColors(homeTeamColor, awayTeamColor, {
        isDark: c.isDark,
        backgroundHex: c.surface,
        fallback: { home: c.teal, away: c.navy },
      }),
    [homeTeamColor, awayTeamColor, c],
  );
  const effectiveColors: ChartColors = useMemo(
    () => ({ ...c, teal: resolved.home, navy: resolved.away }),
    [c, resolved],
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

  const home = useMemo(() => cumulativeSeries(shots, true), [shots]);
  const away = useMemo(() => cumulativeSeries(shots, false), [shots]);
  const endMinute = useMemo(
    () =>
      Math.max(
        90,
        ...shots
          .filter((s) => s.period !== "PenaltyShootout")
          .map((s) => s.minute ?? 0),
      ),
    [shots],
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
    () => buildOption(home, away, homeName, awayName, endMinute, mode, effectiveColors),
    [home, away, homeName, awayName, endMinute, mode, effectiveColors],
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
          showSummary={mode !== "export"}
        />
      ) : (
        <div style={{ height: height ?? 260 }} aria-hidden />
      )}
    </div>
  );
}
