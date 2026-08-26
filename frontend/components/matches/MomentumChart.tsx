"use client";

/**
 * 势头图 —— FotMob 自己算的逐分钟综合评分(黑箱,方法论未公开),正值主队
 * 占优、负值客队占优,以零线为轴上下填色的连续曲线。
 *
 * 2026-08-23 对照 FotMob 官方安卓包核实数据结构后落地:content.momentum.
 * main.data,真实比赛(5107575)用进球事件反向验证过正负号含义
 * (backend/fotmob_client.py::parse_momentum_records 有完整推导过程)。
 *
 * **必须与「射门威胁时间轴」(ThreatTimeline.tsx)明确区分,不能互相替代**:
 * 射门威胁是本站自己按 fact_shotmap 的 xG 现算的,口径透明、可复现,只在
 * 有射门的时段才有柱子;这张势头图是 FotMob 自己的综合评分,把传球、控球、
 * 推进等我们库里没有的逐次动作也计了进去,口径不透明。两张图对同一段比赛
 * 给出不同甚至相反的结论是正常的——不是矛盾,是两种不同的度量方式。footNote
 * 必须把这条说清楚,不能让读者以为两张图应该一致。
 *
 * 2026-08-23 起才采集,旧场次/未回填场次没有数据,调用方在 momentum 为空
 * 数组时不应渲染本组件(同其它按数据可用性决定是否渲染的 Section 一致)。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type { EChartsOption } from "echarts";
import { EChart } from "@/components/EChart";
import type { ChartMode } from "@/components/charts/chartMode";
import { useChartColors, type ChartColors } from "@/components/charts/useChartColors";
import { resolveMatchColors, type TeamColorPair } from "@/components/charts/matchTeamColors";
import type { MatchReportResponse } from "@/lib/api-v1";
import styles from "./MomentumChart.module.css";

type MatchReport = Extract<MatchReportResponse, { available: true }>;
type MomentumPoint = MatchReport["momentum"][number];

/** 按分钟排序(API 已经按 Minute 排,这里不信任调用方顺序)+ 算主客占优
 * 分钟数(用于文字摘要和"谁全场更占优"的粗略判断)。抽成纯函数便于测试——
 * ECharts 组件本身不做整体渲染测试(项目既有惯例,见 ThreatTimeline/
 * XgRaceChart 只测 buildBuckets/cumulativeSeries 这类纯逻辑)。 */
export function summarizeMomentum(momentum: MomentumPoint[]): {
  points: MomentumPoint[];
  endMinute: number;
  homeShare: number;
  awayShare: number;
} {
  const points = [...momentum].sort((a, b) => a.minute - b.minute);
  const endMinute = points.length ? Math.max(90, ...points.map((p) => p.minute)) : 90;
  const homeShare = points.filter((p) => p.value > 0).length;
  const awayShare = points.filter((p) => p.value < 0).length;
  return { points, endMinute, homeShare, awayShare };
}

/** 导出供渲染冒烟测试直接调用(frontend/tests/chart-render-smoke.test.ts)
 * ——组件里的纯函数从不被真的渲染过是 2026-08-24 势头图崩溃能一路上线的
 * 根本原因(vitest 424/424 全绿但线上白屏),见 CLAUDE.md §11.3。 */
export function buildOption(
  points: MomentumPoint[],
  endMinute: number,
  mode: ChartMode,
  c: ChartColors,
): EChartsOption {
  const bound = Math.max(20, ...points.map((p) => Math.abs(p.value)));
  return {
    grid: {
      left: mode === "export" ? 60 : 30,
      right: mode === "export" ? 32 : 14,
      top: mode === "export" ? 40 : 16,
      bottom: mode === "export" ? 46 : 26,
    },
    tooltip:
      mode === "export"
        ? undefined
        : {
            trigger: "axis",
            confine: true,
            formatter: (params: unknown) => {
              const rows = params as Array<{ value: [number, number] }>;
              if (!rows?.length) return "";
              const [minute, value] = rows[0].value;
              const side = value > 0 ? "主队占优" : value < 0 ? "客队占优" : "均势";
              return `第 ${minute}′<br/>${side}(${Math.abs(value).toFixed(0)})`;
            },
          },
    xAxis: {
      type: "value",
      min: 0,
      max: endMinute,
      interval: 15,
      axisLabel: { color: c.ink2, fontSize: 11, formatter: (v: number) => `${v}′` },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      show: false,
      min: (v: { min: number }) => Math.min(-20, v.min),
      max: (v: { max: number }) => Math.max(20, v.max),
    },
    // 2026-08-24:pieces 必须给闭区间。项目用的 echarts ^6.1.0 对只给
    // min 或只给 max 的开区间(即使加 type:'piecewise'/gte/lt 也一样)会在
    // MarkLineView 里抛 `Cannot read properties of undefined (reading 'coord')`,
    // 导致整张图一个像素都不画、且异常会冒泡到 React 错误边界把整个比赛详情页
    // 变成"页面出错了"(线上实测复现)。边界必须是从数据算出的有限值,不能留空。
    visualMap: {
      show: false,
      type: "piecewise",
      dimension: 1,
      pieces: [
        { min: 0, max: bound, color: c.teal },
        { min: -bound, max: 0, color: c.navy },
      ],
    },
    series: [
      {
        type: "line",
        data: points.map((p) => [p.minute, p.value]),
        showSymbol: false,
        smooth: 0.15,
        lineStyle: { width: 1.5 },
        areaStyle: { opacity: 0.35 },
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { color: c.ink3, opacity: 0.4, width: 1 },
          label: { show: false },
          data: [{ yAxis: 0 }],
        },
      },
    ],
  };
}

export function MomentumChart({
  momentum,
  homeName,
  awayName,
  homeTeamColor,
  awayTeamColor,
  mode = "interactive",
  height,
}: {
  momentum: MatchReport["momentum"];
  homeName: string;
  awayName: string;
  /** 2026-08-24:真实球队配色,缺失或对比度不达标时回退品牌青绿/蓝。 */
  homeTeamColor?: TeamColorPair | null;
  awayTeamColor?: TeamColorPair | null;
  mode?: ChartMode;
  height?: number;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(mode === "export");
  const c = useChartColors();
  // 势头图铺满卡片背景(--surface),不是中性球场底——每个图表对着自己的
  // 真实渲染背景算对比度,见 components/charts/matchTeamColors.ts 模块注释。
  // 包 useMemo:resolveMatchColors 有实际计算(十六进制校验+对比度数学),
  // 不应该在 c 引用不变时(hook 自己已做主题不变则同引用的缓存)每次渲染
  // 重算一遍,也不应该让下面 option 的 useMemo 因为新对象引用而失效。
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

  const { points, endMinute, homeShare, awayShare } = useMemo(
    () => summarizeMomentum(momentum),
    [momentum],
  );

  const ariaSummary = useMemo(() => {
    if (points.length === 0) return "本场没有势头数据。";
    const lead = homeShare >= awayShare ? homeName : awayName;
    return (
      `势头图:全场 ${lead} 占优时段更多(${homeShare} 分钟 vs ${awayShare} 分钟)。` +
      `与「射门威胁时间轴」是两种不同的度量方式,数值不必一致。`
    );
  }, [points, homeShare, awayShare, homeName, awayName]);

  const option = useMemo(
    () => buildOption(points, endMinute, mode, effectiveColors),
    [points, endMinute, mode, effectiveColors],
  );

  if (points.length === 0) return null;

  return (
    <div ref={wrapRef}>
      {visible ? (
        <EChart
          option={option}
          height={height ?? (mode === "export" ? 300 : 180)}
          ariaSummary={ariaSummary}
          mode={mode}
          showSummary={mode !== "export"}
        />
      ) : (
        <div style={{ height: height ?? 180 }} aria-hidden />
      )}
      {mode !== "export" && (
        <>
          <div className={styles.legend}>
            <span className={styles.legendItem}>
              <i className={styles.homeDot} style={{ background: resolved.home }} />
              {homeName} 占优
            </span>
            <span className={styles.legendItem}>
              <i className={styles.awayDot} style={{ background: resolved.away }} />
              {awayName} 占优
            </span>
          </div>
          <p className={styles.footNote}>
            数据来自 FotMob 官方综合评分——与「射门威胁时间轴」是两种不同的度量方式,
            两图走势不一致是正常的,不代表其中一张错了。
          </p>
        </>
      )}
    </div>
  );
}
