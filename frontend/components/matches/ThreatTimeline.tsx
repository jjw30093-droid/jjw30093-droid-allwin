"use client";

/**
 * 射门威胁时间轴 —— 以中线为轴的双向柱状图,向上是主队、向下是客队,
 * 每根柱子代表一个时间段内该队射门的 xG 合计,进球分钟另打标记。
 *
 * 视觉语法借鉴 Sofascore / Opta 的 Attack Momentum(双向柱是"让普通人看懂
 * 高阶数据"的黄金范式:不需要知道 xG 是什么,柱子高低就是"这段谁在打谁")。
 *
 * **但必须如实区分,不能叫 Attack Momentum**:Opta 那套对每一次控球序列
 * 估值(传球、推进、夺回都计分),我们库里没有逐次控球数据
 * (BallPossesion 只有整场与半场两种粒度),只有射门。所以:
 * - 本图只在有射门的时间段才有柱子,无射门时段是空的;
 * - 标题与摘要一律写"射门威胁",不写"控球动量"。
 *
 * 数据来源:fact_shotmap 330,217 行 / 13,038 场,Minute 100% 非空、
 * xG 99.7% 非空 —— 13,029/13,051 场(99.83%)可画。后端零改动,
 * MatchReportShot DTO 已下发 minute / xg / is_home / outcome / player_name。
 *
 * 点球大战(Period='PenaltyShootout')排除:不属于比赛进程。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type { EChartsOption } from "echarts";
import { EChart } from "@/components/EChart";
import type { ChartMode } from "@/components/charts/chartMode";
import { tokensFor } from "@/components/charts/chartMode";
import type { MatchReportResponse } from "@/lib/api-v1";
import styles from "./ThreatTimeline.module.css";

type MatchReport = Extract<MatchReportResponse, { available: true }>;
type Shot = MatchReport["shots"][number];

const GOLD = "#d49e33";
const INK2 = "#a79c87";
const GOAL = "#4e9a5b";

/** 可选时间粒度。5 分钟接近逐分钟的观感,15 分钟适合窄屏/卡片。 */
const BUCKETS = [5, 15] as const;
type BucketSize = (typeof BUCKETS)[number];

type Bucket = {
  start: number;
  homeXg: number;
  awayXg: number;
  homeGoals: Shot[];
  awayGoals: Shot[];
};

export function buildBuckets(shots: Shot[], size: number): Bucket[] {
  const inPlay = shots.filter(
    (s) => s.period !== "PenaltyShootout" && s.minute != null,
  );
  if (inPlay.length === 0) return [];
  const maxMinute = Math.max(90, ...inPlay.map((s) => s.minute ?? 0));
  const count = Math.ceil((maxMinute + 1) / size);
  const buckets: Bucket[] = Array.from({ length: count }, (_, i) => ({
    start: i * size,
    homeXg: 0,
    awayXg: 0,
    homeGoals: [],
    awayGoals: [],
  }));
  for (const s of inPlay) {
    const b = buckets[Math.floor((s.minute ?? 0) / size)];
    if (!b) continue;
    // xG 缺失(全库 0.3%)按 0 计入柱高,但射门本身仍然存在 —— 不因此丢掉进球标记
    const xg = s.xg ?? 0;
    if (s.is_home) {
      b.homeXg += xg;
      if (s.outcome === "Goal") b.homeGoals.push(s);
    } else {
      b.awayXg += xg;
      if (s.outcome === "Goal") b.awayGoals.push(s);
    }
  }
  return buckets;
}

function summarize(
  buckets: Bucket[],
  homeName: string,
  awayName: string,
  size: number,
): string {
  const homeTotal = buckets.reduce((a, b) => a + b.homeXg, 0);
  const awayTotal = buckets.reduce((a, b) => a + b.awayXg, 0);
  const peak = buckets.reduce<{ side: string; v: number; start: number }>(
    (best, b) => {
      if (b.homeXg > best.v) return { side: homeName, v: b.homeXg, start: b.start };
      if (b.awayXg > best.v) return { side: awayName, v: b.awayXg, start: b.start };
      return best;
    },
    { side: "", v: 0, start: 0 },
  );
  const head = `射门威胁时间轴(每 ${size} 分钟):${homeName} 全场射门 xG 合计 ${homeTotal.toFixed(2)},${awayName} ${awayTotal.toFixed(2)}`;
  const tail = peak.v > 0
    ? `;威胁最集中的一段是第 ${peak.start}–${peak.start + size} 分钟的${peak.side}(${peak.v.toFixed(2)})。`
    : "。";
  return `${head}${tail}仅统计射门,无射门的时段没有柱子。`;
}

function buildOption(
  buckets: Bucket[],
  homeName: string,
  awayName: string,
  size: number,
  mode: ChartMode,
): EChartsOption {
  const t = tokensFor(mode);
  const labels = buckets.map((b) => `${b.start}'`);
  // 同一时段的多个进球合成一个标记(名字用顿号连),否则会在同一坐标叠成一坨;
  // 主客分成两个 series,标签分别朝上/朝下 —— 负值柱上 position:"top" 是朝
  // 零线方向,会和主队标签互相压叠。
  const goalMarks = (side: "home" | "away") =>
    buckets
      .map((b) => {
        const goals = side === "home" ? b.homeGoals : b.awayGoals;
        if (goals.length === 0) return null;
        // 同人同段梅开二度 → "帕尔默 ×2";不同人 → 只显示第一个 + 计数。
        // 不做全名拼接:最末一格的长标签会被网格右边界截断。
        const names = goals.map((g) => g.player_name).filter(Boolean) as string[];
        const uniq = Array.from(new Set(names));
        const label =
          names.length <= 1
            ? names[0] ?? "进球"
            : uniq.length === 1
              ? `${uniq[0]} ×${names.length}`
              : `${uniq[0]} 等 ${names.length} 球`;
        return {
          value: [`${b.start}'`, side === "home" ? b.homeXg : -b.awayXg],
          name: label,
        };
      })
      .filter((x): x is { value: [string, number]; name: string } => x !== null);

  return {
    grid: {
      // 左右留白要容得下首/末时段的进球球员名,否则标签被网格边界截断
      left: mode === "export" ? 96 : 50,
      right: mode === "export" ? 72 : 34,
      top: mode === "export" ? 70 : 34,
      bottom: mode === "export" ? 66 : 28,
    },
    legend: {
      // 只列两队,不把"主队进球/客队进球"两个标记 series 放进图例(它们是注记不是维度)
      data: [homeName, awayName],
      textStyle: { color: INK2, fontSize: t.legendFont },
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
            axisPointer: { type: "shadow" },
            formatter: (params: unknown) => {
              const rows = params as Array<{
                axisValue: string;
                seriesName: string;
                value: number;
              }>;
              if (!rows?.length) return "";
              const min = rows[0].axisValue;
              const body = rows
                .map((r) => `${r.seriesName} xG ${Math.abs(r.value).toFixed(2)}`)
                .join("<br/>");
              return `第 ${min} 起 ${size} 分钟<br/>${body}`;
            },
          },
    xAxis: {
      type: "category",
      data: labels,
      axisLabel: {
        color: INK2,
        fontSize: t.axisFont,
        // 窄屏/大字号下只标整刻度,避免标签互相压叠
        interval: (index: number) => buckets[index].start % (size >= 15 ? 15 : 15) === 0,
      },
      axisTick: { show: false },
    },
    yAxis: {
      type: "value",
      // 中线为轴:上正下负;刻度取绝对值,避免出现"-0.5 的 xG"这种读不通的标签
      axisLabel: {
        color: INK2,
        fontSize: t.axisFont,
        formatter: (v: number) => Math.abs(v).toFixed(1),
      },
      splitLine: { lineStyle: { opacity: 0.15 } },
    },
    series: [
      {
        name: homeName,
        type: "bar",
        stack: "threat",
        data: buckets.map((b) => b.homeXg),
        color: GOLD,
        barCategoryGap: "20%",
      },
      {
        name: awayName,
        type: "bar",
        stack: "threat",
        data: buckets.map((b) => -b.awayXg),
        color: INK2,
        barCategoryGap: "20%",
      },
      ...(["home", "away"] as const).map((side) => ({
        name: side === "home" ? "主队进球" : "客队进球",
        type: "scatter" as const,
        data: goalMarks(side),
        symbol: "circle",
        symbolSize: t.symbolSize + 4,
        itemStyle: { color: GOAL, borderColor: "#fff", borderWidth: 2 },
        label: {
          show: true,
          // 主队柱向上 → 标签在上;客队柱向下 → 标签必须在下,否则挤向零线互相压叠
          position: side === "home" ? ("top" as const) : ("bottom" as const),
          fontSize: Math.round(t.axisFont * 0.95),
          color: GOAL,
          formatter: ({ name }: { name: string }) => name,
        },
        // 相邻时段的进球标签会横向碰撞;重叠时隐藏文字(绿点保留,信息不丢)
        labelLayout: { hideOverlap: true },
        tooltip: { show: false },
        legendHoverLink: false,
        silent: true,
      })),
    ],
  };
}

export function ThreatTimeline({
  shots,
  homeName,
  awayName,
  mode = "interactive",
  height,
}: {
  shots: MatchReport["shots"];
  homeName: string;
  awayName: string;
  mode?: ChartMode;
  height?: number;
}) {
  const [size, setSize] = useState<BucketSize>(5);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(mode === "export");

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

  const effectiveSize = mode === "export" ? 5 : size;
  const buckets = useMemo(
    () => buildBuckets(shots, effectiveSize),
    [shots, effectiveSize],
  );
  const ariaSummary = useMemo(
    () => summarize(buckets, homeName, awayName, effectiveSize),
    [buckets, homeName, awayName, effectiveSize],
  );
  const option = useMemo(
    () => buildOption(buckets, homeName, awayName, effectiveSize, mode),
    [buckets, homeName, awayName, effectiveSize, mode],
  );

  if (buckets.length === 0) {
    return <p className={styles.empty}>本场没有可用的射门时间数据。</p>;
  }

  return (
    <div className={styles.wrap} ref={wrapRef}>
      {mode !== "export" && (
        <div className={styles.controls}>
          <span className={styles.controlLabel}>时间粒度</span>
          <div className={styles.segmented}>
            {BUCKETS.map((b) => (
              <button
                key={b}
                type="button"
                className={b === size ? styles.active : undefined}
                aria-pressed={b === size}
                onClick={() => setSize(b)}
              >
                {b} 分钟
              </button>
            ))}
          </div>
        </div>
      )}
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
