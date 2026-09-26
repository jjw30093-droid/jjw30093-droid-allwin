"use client";

/**
 * 比赛详情图表取色的唯一挂载点(2026-09-26 第四批):把 resolveMatchColors 接到
 * useChartColors()(主题 + 真实背景色)上,返回
 *   - resolved:主客色 + 来自哪一级 + 是否经明度微调;
 *   - effectiveColors:把 c.teal / c.navy 槽位换成主 / 客色的 ChartColors
 *     (图表构造 option 时一直是这样读主客色的);
 * 各图表组件只调这一个 hook,不再各自写取色 / 兜底 / 主客区分检查。
 *
 * background:"surface" = 卡片底(--surface);"pitch" = 射门类图的中性球场底
 * (--pitch-neutral-bg)。对比度必须对着图表真实渲染背景算。
 */

import { useMemo } from "react";
import {
  resolveMatchColors,
  type ResolvedMatchColors,
  type TeamBrandColor,
  type TeamColorPair,
} from "./matchTeamColors";
import { useChartColors, type ChartColors } from "./useChartColors";

export type MatchColorProps = {
  /** 本场 FotMob 配色(配对级) */
  homeTeamColor?: TeamColorPair | null;
  awayTeamColor?: TeamColorPair | null;
  /** 该队近期代表色(本场配色缺失或校验不过时的第二级) */
  homeTeamBrandColor?: TeamBrandColor | null;
  awayTeamBrandColor?: TeamBrandColor | null;
};

export function useMatchColors(
  props: MatchColorProps,
  background: "surface" | "pitch",
): { c: ChartColors; resolved: ResolvedMatchColors; effectiveColors: ChartColors } {
  const c = useChartColors();
  const { homeTeamColor, awayTeamColor, homeTeamBrandColor, awayTeamBrandColor } = props;
  const backgroundHex = background === "pitch" ? c.pitchBg : c.surface;
  const resolved = useMemo(
    () =>
      resolveMatchColors(
        {
          home: { match: homeTeamColor, team: homeTeamBrandColor },
          away: { match: awayTeamColor, team: awayTeamBrandColor },
        },
        { isDark: c.isDark, backgroundHex },
      ),
    [homeTeamColor, awayTeamColor, homeTeamBrandColor, awayTeamBrandColor, c.isDark, backgroundHex],
  );
  const effectiveColors = useMemo(
    () => ({ ...c, teal: resolved.home, navy: resolved.away }),
    [c, resolved],
  );
  return { c, resolved, effectiveColors };
}
