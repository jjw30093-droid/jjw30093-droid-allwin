"use client";

/**
 * 进攻区域三分带(2026-08-25,对齐 FotMob attackingZones):左/中/右三路
 * 各占该队进攻的百分比,主客各画在自己进攻的半场里。
 *
 * 纯内联 SVG(底图复用 FootballPitchBackground variant="neutral",同
 * ShotMapChart 的 0..105/0..68 坐标域),不用 ECharts——这张图没有坐标轴、
 * 比例尺、交互和数据驱动布局,只有 6 个固定色带和数字,用 ECharts 等于拿
 * 柱状图假装球场,还要背 §11.3 的 buildOption + headless 冒烟税。
 *
 * 三个已定死的细节(方案 §6.3):
 * 1. 箭头方向与射门图一致:主队攻向右、客队攻向左(同一个 tab 里两张图
 *    不能指向矛盾)。攻向右时"左路"在画面上方(面朝右,左手边是上),
 *    攻向左时相反。
 * 2. 百分比徽章用绝对定位 HTML,不用 SVG <text>——底图
 *    preserveAspectRatio="none",父级约束下 SVG 文字会被非等比拉伸
 *    (VerticalPitchFormation 的既有写法)。
 * 3. left/center/right 是内部枚举不得直出(§11.2)→ 左路/中路/右路,
 *    字号 ≥12px。
 *
 * 色带透明度按占比缩放,是数字徽章之外的冗余编码——数值本身始终以文字
 * 展示(徽章 + 下方文字摘要),不依赖色深读数。
 */

import { useState } from "react";
import { useChartColors } from "@/components/charts/useChartColors";
import { resolveMatchColors, type TeamColorPair } from "@/components/charts/matchTeamColors";
import { hexToRgba } from "@/components/charts/useChartColors";
import { FootballPitchBackground } from "./FootballPitchBackground";
import {
  buildAttackingZonesSummary,
  type AttackingZoneSplit,
} from "./attackingZones";
import styles from "./AttackingZonesChart.module.css";

export type { AttackingZoneSplit } from "./attackingZones";

type PeriodKey = "All" | "FirstHalf" | "SecondHalf";

const PERIOD_LABEL: Record<PeriodKey, string> = {
  All: "全场",
  FirstHalf: "上半场",
  SecondHalf: "下半场",
};

const ZONE_LABEL = { left: "左路", center: "中路", right: "右路" } as const;
type ZoneKey = keyof typeof ZONE_LABEL;

/** 某时段两侧是否完全无数据。 */
function empty(h: AttackingZoneSplit | null, a: AttackingZoneSplit | null): boolean {
  return h == null && a == null;
}

export function AttackingZonesChart({
  home,
  away,
  homeName,
  awayName,
  homeTeamColor,
  awayTeamColor,
  byPeriod,
}: {
  /** 该时段的三分区占比;某侧缺失传 null(不补 0)。 */
  home: AttackingZoneSplit | null;
  away: AttackingZoneSplit | null;
  homeName: string;
  awayName: string;
  homeTeamColor?: TeamColorPair | null;
  awayTeamColor?: TeamColorPair | null;
  /** 上/下半场;缺失时组件不渲染时段切换器(同 MatchStatsSection 的
   * hasHalves 诚实模式)。 */
  byPeriod?: {
    FirstHalf?: { home: AttackingZoneSplit | null; away: AttackingZoneSplit | null };
    SecondHalf?: { home: AttackingZoneSplit | null; away: AttackingZoneSplit | null };
  };
} ) {
  const c = useChartColors();
  const [period, setPeriod] = useState<PeriodKey>("All");
  const hasHalves =
    !empty(byPeriod?.FirstHalf?.home ?? null, byPeriod?.FirstHalf?.away ?? null) ||
    !empty(byPeriod?.SecondHalf?.home ?? null, byPeriod?.SecondHalf?.away ?? null);

  // 两侧全场都缺、半场也没有 → 整个组件诚实不渲染(不画空球场)
  if (empty(home, away) && !hasHalves) return null;

  const active =
    period === "All"
      ? { home, away }
      : {
          home: byPeriod?.[period]?.home ?? null,
          away: byPeriod?.[period]?.away ?? null,
        };

  const resolved = resolveMatchColors(homeTeamColor, awayTeamColor, {
    isDark: c.isDark,
    backgroundHex: c.pitchBg,
    fallback: { home: c.teal, away: c.navy },
  });

  const summary = buildAttackingZonesSummary({
    home: active.home,
    away: active.away,
    homeName,
    awayName,
    periodLabel: PERIOD_LABEL[period],
  });

  // 三条横带:上/中/下各 1/3。攻向右(主队,右半场)左路在上;
  // 攻向左(客队,左半场)左路在下(见模块注释细节 1)。
  const rowsHome: ZoneKey[] = ["left", "center", "right"];
  const rowsAway: ZoneKey[] = ["right", "center", "left"];
  const alphaFor = (v: number) => Math.min(0.85, Math.max(0.1, v / 100));

  const bands = (
    side: "home" | "away",
    split: AttackingZoneSplit | null,
  ) => {
    if (!split) return null;
    const rows = side === "home" ? rowsHome : rowsAway;
    const x = side === "home" ? 52.5 : 0;
    const color = side === "home" ? resolved.home : resolved.away;
    return rows.map((zone, i) => (
      <rect
        key={`${side}-${zone}`}
        x={x}
        y={(68 / 3) * i}
        width={52.5}
        height={68 / 3}
        fill={hexToRgba(color, alphaFor(split[zone]))}
      />
    ));
  };

  const badges = (side: "home" | "away", split: AttackingZoneSplit | null) => {
    if (!split) return null;
    const rows = side === "home" ? rowsHome : rowsAway;
    const leftPct = side === "home" ? 75 : 25;
    return rows.map((zone, i) => (
      <div
        key={`${side}-${zone}`}
        className={styles.badge}
        style={{ left: `${leftPct}%`, top: `${(100 / 3) * i + 100 / 6}%` }}
      >
        <span className={styles.badgeLabel}>{ZONE_LABEL[zone]}</span>
        <span className={`${styles.badgeValue} num`}>{split[zone]}%</span>
      </div>
    ));
  };

  return (
    <div className={styles.wrap}>
      {hasHalves && (
        <div className={styles.periodSegmented} role="tablist" aria-label="切换时段">
          {(Object.keys(PERIOD_LABEL) as PeriodKey[]).map((key) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={period === key}
              className={period === key ? styles.periodOn : styles.periodOff}
              onClick={() => setPeriod(key)}
            >
              {PERIOD_LABEL[key]}
            </button>
          ))}
        </div>
      )}
      <div className={styles.legend}>
        <span style={{ color: resolved.home }} className={styles.legendHome}>
          {homeName}(攻向右 →)
        </span>
        <span style={{ color: resolved.away }} className={styles.legendAway}>
          {awayName}(← 攻向左)
        </span>
      </div>
      <div className={styles.pitchWrap}>
        <FootballPitchBackground variant="neutral" />
        <svg
          viewBox="0 0 105 68"
          preserveAspectRatio="none"
          aria-hidden
          className={styles.bandLayer}
        >
          {bands("away", active.away)}
          {bands("home", active.home)}
        </svg>
        {badges("away", active.away)}
        {badges("home", active.home)}
        {empty(active.home, active.away) && (
          <p className={styles.noData}>该时段暂无进攻区域数据。</p>
        )}
      </div>
      <p className="chart-summary">{summary}</p>
    </div>
  );
}
