"use client";

/**
 * 联赛速览四图 —— 消费四张早已构建但前端零消费的银层表
 * (silver_goal_minute_buckets / silver_score_distribution /
 *  silver_over_under_thresholds / silver_league_season_summary)。
 *
 * 三个公开联赛页此前 EChart import 数 = 0,是全站图表化程度最低的区域。
 *
 * 四张图都刻意选"竞彩用户本来就在用的语言":进球时段、常见比分、大小球
 * 阈值、主客胜率 —— 不需要理解 xG 之类的高阶指标就能看懂,而且每一张
 * 都能独立成为一条短视频/小红书素材。
 */

import type { EChartsOption } from "echarts";
import { EChart } from "@/components/EChart";
import { useChartColors, hexToRgba } from "@/components/charts/useChartColors";
import type { GetJson } from "@/lib/api-v1";
import styles from "./SeasonProfileCharts.module.css";

type Profile = GetJson<"/api/v1/leagues/{league_id}/season-profile">;

/* ── 1. 进球时段 ─────────────────────────────────────────── */

function GoalMinutes({ rows }: { rows: Profile["goal_minutes"] }) {
  const c = useChartColors();
  const axis = { color: c.ink2, fontSize: 12 } as const;
  if (!rows?.length) return null;
  const peak = rows.reduce((a, b) => ((b.pct ?? 0) > (a.pct ?? 0) ? b : a));
  const option: EChartsOption = {
    grid: { left: 46, right: 16, top: 16, bottom: 28 },
    xAxis: { type: "category", data: rows.map((r) => r.bucket), axisLabel: axis },
    yAxis: {
      type: "value",
      axisLabel: { ...axis, formatter: (v: number) => `${v}%` },
      splitLine: { lineStyle: { opacity: 0.15 } },
    },
    tooltip: {
      confine: true,
      trigger: "axis",
      formatter: (p: unknown) => {
        const rows_ = p as Array<{ axisValue: string; dataIndex: number }>;
        const r = rows[rows_[0].dataIndex];
        return `${r.bucket} 分钟<br/>${r.goal_count} 球(占 ${r.pct}%)`;
      },
    },
    series: [
      {
        type: "bar",
        data: rows.map((r) => ({
          value: r.pct ?? 0,
          // 进球最集中的那一段高亮 —— 这就是这张图的"话题点"
          itemStyle: { color: r.bucket === peak.bucket ? c.teal : c.ink2 },
        })),
        barWidth: "58%",
        label: {
          show: true,
          position: "top",
          color: c.ink2,
          fontSize: 12,
          formatter: ({ value }: { value: unknown }) => `${value}%`,
        },
      },
    ],
  };
  return (
    <ChartCard
      title="进球时段分布"
      hint={`进球最集中的时段是 ${peak.bucket} 分钟,占全部进球的 ${peak.pct}%`}
    >
      <EChart
        option={option}
        height={220}
        ariaSummary={
          `进球时段分布:` +
          rows.map((r) => `${r.bucket} 分钟 ${r.goal_count} 球(${r.pct}%)`).join(";") +
          `。最集中的是 ${peak.bucket} 分钟。`
        }
      />
    </ChartCard>
  );
}

/* ── 2. 比分分布(热力网格) ───────────────────────────────── */

const MAX_SCORE = 4; // 0-4 球之外合并进"其他",避免窄屏挤成马赛克

function ScoreGrid({ rows }: { rows: Profile["score_distribution"] }) {
  const c = useChartColors();
  const axis = { color: c.ink2, fontSize: 12 } as const;
  if (!rows?.length) return null;
  const cells: { h: number; a: number; pct: number; count: number }[] = [];
  let other = { pct: 0, count: 0 };
  for (const r of rows) {
    const h = r.home_score ?? 0;
    const a = r.away_score ?? 0;
    if (h > MAX_SCORE || a > MAX_SCORE) {
      other = { pct: other.pct + (r.pct ?? 0), count: other.count + (r.match_count ?? 0) };
    } else {
      cells.push({ h, a, pct: r.pct ?? 0, count: r.match_count ?? 0 });
    }
  }
  const top = rows[0];
  const maxPct = Math.max(...cells.map((cell) => cell.pct), 0.0001);
  const option: EChartsOption = {
    grid: { left: 46, right: 20, top: 26, bottom: 34 },
    xAxis: {
      type: "category",
      data: Array.from({ length: MAX_SCORE + 1 }, (_, i) => String(i)),
      name: "客队",
      nameTextStyle: axis,
      axisLabel: axis,
      splitArea: { show: true },
    },
    yAxis: {
      type: "category",
      data: Array.from({ length: MAX_SCORE + 1 }, (_, i) => String(i)),
      name: "主队",
      nameTextStyle: axis,
      axisLabel: axis,
      splitArea: { show: true },
    },
    tooltip: {
      confine: true,
      formatter: (p: unknown) => {
        const d = (p as { data: [number, number, number] }).data;
        const cell = cells.find((item) => item.a === d[0] && item.h === d[1]);
        return cell
          ? `${cell.h}–${cell.a}<br/>${cell.count} 场(占 ${cell.pct}%)`
          : "";
      },
    },
    visualMap: {
      min: 0,
      max: maxPct,
      show: false,
      inRange: { color: [hexToRgba(c.teal, 0.08), c.teal] },
    },
    series: [
      {
        type: "heatmap",
        data: cells.map((cell) => [cell.a, cell.h, cell.pct]),
        label: {
          show: true,
          fontSize: 12,
          color: "#fff",
          formatter: ({ value }: { value: unknown }) => {
            const v = (value as [number, number, number])[2];
            return v >= 3 ? `${v.toFixed(0)}%` : "";
          },
        },
      },
    ],
  };
  return (
    <ChartCard
      title="常见比分"
      hint={`最常出现的比分是 ${top.home_score}–${top.away_score},占 ${top.pct}%${
        other.count > 0 ? `;另有 ${other.count} 场比分超出 0–4 区间未在网格内显示` : ""
      }`}
    >
      <EChart
        option={option}
        height={240}
        ariaSummary={
          `常见比分分布(主队纵轴 / 客队横轴):` +
          rows
            .slice(0, 5)
            .map((r) => `${r.home_score}–${r.away_score} 占 ${r.pct}%`)
            .join(";") +
          `。`
        }
      />
    </ChartCard>
  );
}

/* ── 3. 大小球阈值 ───────────────────────────────────────── */

function OverUnder({ rows }: { rows: Profile["over_under"] }) {
  const c = useChartColors();
  const axis = { color: c.ink2, fontSize: 12 } as const;
  if (!rows?.length) return null;
  const option: EChartsOption = {
    grid: { left: 56, right: 20, top: 30, bottom: 26 },
    legend: { data: ["大球", "小球"], textStyle: axis, top: 0 },
    xAxis: {
      type: "value",
      max: 100,
      axisLabel: { ...axis, formatter: (v: number) => `${v}%` },
      splitLine: { lineStyle: { opacity: 0.15 } },
    },
    yAxis: {
      type: "category",
      data: rows.map((r) => `${r.threshold}`),
      axisLabel: axis,
      inverse: true,
    },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    series: [
      {
        name: "大球",
        type: "bar",
        stack: "ou",
        data: rows.map((r) => r.over_pct ?? 0),
        color: c.teal,
        barWidth: 16,
        label: {
          show: true,
          position: "insideLeft",
          color: "#fff",
          fontSize: 12,
          formatter: ({ value }: { value: unknown }) => `${Number(value).toFixed(0)}%`,
        },
      },
      {
        name: "小球",
        type: "bar",
        stack: "ou",
        data: rows.map((r) => r.under_pct ?? 0),
        color: c.ink2,
        barWidth: 16,
      },
    ],
  };
  const r25 = rows.find((r) => r.threshold === 2.5);
  return (
    <ChartCard
      title="大小球命中率"
      hint={
        r25
          ? `最常用的 2.5 球线:大球 ${r25.over_pct}%(${r25.over_count} 场),小球 ${r25.under_pct}%(${r25.under_count} 场)`
          : "按进球总数阈值统计的历史比例"
      }
    >
      <EChart
        option={option}
        height={230}
        ariaSummary={
          `大小球命中率:` +
          rows.map((r) => `${r.threshold} 球线大球 ${r.over_pct}%、小球 ${r.under_pct}%`).join(";") +
          `。为历史统计比例,不是对下一场的预测。`
        }
      />
    </ChartCard>
  );
}

/* ── 4. 主平客 + BTTS ────────────────────────────────────── */

function OutcomeSplit({ summary }: { summary: Profile["summary"] }) {
  const c = useChartColors();
  const axis = { color: c.ink2, fontSize: 12 } as const;
  if (!summary) return null;
  const rows = [
    { name: "主胜", value: summary.home_win_pct ?? 0, color: c.win },
    { name: "平局", value: summary.draw_pct ?? 0, color: c.draw },
    { name: "客胜", value: summary.away_win_pct ?? 0, color: c.loss },
  ];
  const option: EChartsOption = {
    grid: { left: 46, right: 34, top: 10, bottom: 10 },
    xAxis: { type: "value", max: 100, show: false },
    yAxis: {
      type: "category",
      data: rows.map((r) => r.name),
      inverse: true,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: axis,
    },
    series: [
      {
        type: "bar",
        data: rows.map((r) => ({ value: r.value, itemStyle: { color: r.color } })),
        barWidth: 20,
        label: {
          show: true,
          position: "right",
          color: c.ink2,
          fontSize: 12,
          formatter: ({ value }: { value: unknown }) => `${Number(value).toFixed(1)}%`,
        },
      },
    ],
  };
  return (
    <ChartCard
      title="主平客分布"
      hint={`共 ${summary.total_matches ?? 0} 场 · 场均总进球 ${summary.avg_total_goals ?? "—"} · 双方均有进球 ${summary.btts_pct ?? "—"}%`}
    >
      <EChart
        option={option}
        height={170}
        ariaSummary={`主平客分布:主胜 ${summary.home_win_pct}%、平局 ${summary.draw_pct}%、客胜 ${summary.away_win_pct}%;样本 ${summary.total_matches} 场。`}
      />
    </ChartCard>
  );
}

/* ── 布局 ────────────────────────────────────────────────── */

function ChartCard({
  title,
  hint,
  children,
}: {
  title: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <section className={styles.card}>
      <header className={styles.cardHead}>
        <h2>{title}</h2>
        <p>{hint}</p>
      </header>
      {children}
    </section>
  );
}

export function SeasonProfileCharts({ profile }: { profile: Profile }) {
  const hasAny =
    profile.summary != null ||
    (profile.goal_minutes?.length ?? 0) > 0 ||
    (profile.over_under?.length ?? 0) > 0;
  if (!hasAny) {
    return (
      <p className={styles.empty}>
        {profile.empty_reason ?? "该联赛该赛季暂无赛季统计数据。"}
      </p>
    );
  }
  return (
    <div className={styles.grid}>
      <OutcomeSplit summary={profile.summary} />
      <GoalMinutes rows={profile.goal_minutes} />
      <OverUnder rows={profile.over_under} />
      <ScoreGrid rows={profile.score_distribution} />
    </div>
  );
}
