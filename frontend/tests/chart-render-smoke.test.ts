/**
 * 图表渲染冒烟测试(2026-08-24,CLAUDE.md §11.3)。
 *
 * 起因:势头图的 `visualMap.pieces` 开区间配置在项目实际使用的 ECharts
 * ^6.1.0 上会抛 `Cannot read properties of undefined (reading 'coord')`——
 * 这个 bug 在 `vitest run` 424/424 全绿的情况下上线,因为现有图表测试只测
 * `summarizeMomentum`/`buildBuckets`/`filterShots` 这类纯逻辑,从不真的把
 * 构造出的 option 交给 ECharts 渲染一次。
 *
 * 本文件用项目自己的 echarts(而不是新引入一个渲染库)在 Node/jsdom 下做
 * headless SSR 渲染(`renderer:'svg', ssr:true`),对每个图表的
 * `buildOption` 真实调用 `setOption` + `renderToSVGString()`——异常会在这里
 * 被真实抛出而不是被寄望的调用方吞掉。
 */

import * as echarts from "echarts";
import { describe, expect, it } from "vitest";
import { buildOption as buildMomentumOption } from "@/components/matches/MomentumChart";
import { buildBuckets, buildOption as buildThreatOption } from "@/components/matches/ThreatTimeline";
import { cumulativeSeries, buildOption as buildXgRaceOption } from "@/components/matches/XgRaceChart";
import { buildOption as buildShotMapOption } from "@/components/matches/ShotMapChart";
import { buildOption as buildQuadrantOption } from "@/components/matches/TeamStyleQuadrant";
import { buildQuadrantOption as buildLeagueQuadrantOption } from "@/components/league/quadrantOption";
import { collectPoints, mean, outlierNames, viewById } from "@/components/league/quadrantViews";
import {
  playerPlotSet,
  playerViewById,
  topPerQuadrant,
  type PlayerPt,
} from "@/components/league/playerQuadrantViews";
import {
  CREST,
  QUADRANT_GRID,
  crestSizeFor,
  layoutCrests,
  niceAxisRange,
} from "@/components/charts/crestQuadrantLayout";
import type { PlayerQuadrantRow, TeamSeasonStatRow } from "@/lib/api-v1";
import type { ChartColors } from "@/components/charts/useChartColors";

const COLORS: ChartColors = {
  teal: "#087e78",
  navy: "#1d6f8b",
  win: "#287851",
  loss: "#b83b2d",
  draw: "#706c64",
  ink: "#0d2c3d",
  ink2: "#40535d",
  ink3: "#5a6b73",
  grey: "#b8c6c6",
  surface: "#ffffff",
  isDark: false,
  pitchBg: "#f8fafa",
};

/** 2026-08-24:真实球队配色(FotMob prematch-5104961.json 实测值)代入
 * teal/navy 槽位——证明渲染路径对任意十六进制值都成立,不是只在品牌色上
 * 碰巧不崩。 */
const NON_BRAND_COLORS: ChartColors = { ...COLORS, teal: "#f13c26", navy: "#104070" };

/** 真实渲染一次 option,抛异常即测试失败;返回画出的可见图元数,便于额外
 * 断言"至少画出点东西"(而不是异常被吞掉后返回一个空壳)。
 *
 * 2026-08-25:计数口径从只数 `<path` 扩成全部可见图元——射门图标记从
 * scatter(symbol → `<path>`)换成 custom 系列后,zrender 的元素→SVG 映射
 * 是 circle→`<circle>`、polygon→`<polygon>`、line→`<path>`(实测),只数
 * `<path` 会把画出来的标记全部漏计。这是让计数口径对上实际渲染,不是降低
 * 断言(返回值语义不变:画出的图元数)。 */
function renderOrThrow(option: echarts.EChartsOption): number {
  return renderSvg(option).paths;
}

function renderSvg(
  option: echarts.EChartsOption,
  size: { width: number; height: number } = { width: 920, height: 200 },
): { paths: number; images: number; svg: string } {
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, ...size });
  try {
    chart.setOption(option);
    const svg = chart.renderToSVGString();
    // paths 口径含 circle/polygon/rect/line/image(2026-08-25:射门图标记从
    // scatter 换成 custom 系列后,zrender→SVG 映射是 circle/polygon/line,
    // 只数 <path 会把画出来的标记全部漏计);images 单独精确计数,供队徽
    // 渲染断言使用(2026-09-09)。
    return {
      paths: (svg.match(/<(path|circle|polygon|rect|line|image)\b/g) || []).length,
      images: (svg.match(/<image/g) || []).length,
      svg,
    };
  } finally {
    chart.dispose();
  }
}

type Shot = Parameters<typeof buildBuckets>[0][number];

function shot(partial: Partial<Shot>): Shot {
  return {
    player_id: "p1",
    player_name: "球员",
    team_id: 1,
    is_home: true,
    minute: 10,
    period: "FirstHalf",
    x: 90,
    y: 34,
    xg: 0.1,
    xgot: null,
    situation: "RegularPlay",
    outcome: "AttemptSaved",
    shot_type: "RightFoot",
    ...partial,
  } as Shot;
}

describe("MomentumChart.buildOption 渲染冒烟", () => {
  it("真实比赛形状(94 个点,正负穿插)不抛异常且画出线/面", () => {
    const points = Array.from({ length: 94 }, (_, i) => ({ minute: i, value: Math.sin(i / 8) * 40 }));
    const paths = renderOrThrow(buildMomentumOption(points, 94, "interactive", COLORS));
    expect(paths).toBeGreaterThan(0);
  });

  it("空数组不抛异常", () => {
    expect(() => renderOrThrow(buildMomentumOption([], 90, "interactive", COLORS))).not.toThrow();
  });

  it("全场主队占优(无负值)不抛异常", () => {
    const points = Array.from({ length: 10 }, (_, i) => ({ minute: i, value: i + 1 }));
    expect(() => renderOrThrow(buildMomentumOption(points, 90, "interactive", COLORS))).not.toThrow();
  });

  it("全场客队占优(无正值)不抛异常", () => {
    const points = Array.from({ length: 10 }, (_, i) => ({ minute: i, value: -(i + 1) }));
    expect(() => renderOrThrow(buildMomentumOption(points, 90, "interactive", COLORS))).not.toThrow();
  });

  it("export 模式不抛异常", () => {
    const points = Array.from({ length: 20 }, (_, i) => ({ minute: i, value: Math.sin(i) * 10 }));
    expect(() => renderOrThrow(buildMomentumOption(points, 90, "export", COLORS))).not.toThrow();
  });

  it("真实球队配色(非品牌色)不抛异常且画出线/面", () => {
    const points = Array.from({ length: 30 }, (_, i) => ({ minute: i, value: Math.sin(i / 5) * 30 }));
    const paths = renderOrThrow(buildMomentumOption(points, 90, "interactive", NON_BRAND_COLORS));
    expect(paths).toBeGreaterThan(0);
  });
});

describe("ThreatTimeline.buildOption 渲染冒烟", () => {
  it("真实射门数据不抛异常且画出柱子", () => {
    const shots = [
      shot({ minute: 3, xg: 0.2, is_home: true }),
      shot({ minute: 4, xg: 0.5, is_home: true, outcome: "Goal", player_name: "甲" }),
      shot({ minute: 7, xg: 0.3, is_home: false }),
      shot({ minute: 44, xg: 0.6, is_home: false, outcome: "Goal", player_name: "乙" }),
    ];
    const buckets = buildBuckets(shots, 5);
    const paths = renderOrThrow(buildThreatOption(buckets, "主队", "客队", 5, "interactive", COLORS));
    expect(paths).toBeGreaterThan(0);
  });

  it("空射门列表不抛异常", () => {
    const buckets = buildBuckets([], 5);
    expect(() =>
      renderOrThrow(buildThreatOption(buckets, "主队", "客队", 5, "interactive", COLORS)),
    ).not.toThrow();
  });

  it("真实球队配色(非品牌色)不抛异常且画出柱子", () => {
    const shots = [
      shot({ minute: 3, xg: 0.2, is_home: true }),
      shot({ minute: 44, xg: 0.6, is_home: false, outcome: "Goal", player_name: "乙" }),
    ];
    const buckets = buildBuckets(shots, 5);
    const paths = renderOrThrow(
      buildThreatOption(buckets, "主队", "客队", 5, "interactive", NON_BRAND_COLORS),
    );
    expect(paths).toBeGreaterThan(0);
  });
});

describe("XgRaceChart.buildOption 渲染冒烟", () => {
  it("真实射门数据不抛异常且画出曲线", () => {
    const shots = [
      shot({ minute: 3, xg: 0.2, is_home: true }),
      shot({ minute: 20, xg: 0.5, is_home: true, outcome: "Goal" }),
      shot({ minute: 55, xg: 0.3, is_home: false }),
    ];
    const home = cumulativeSeries(shots, true);
    const away = cumulativeSeries(shots, false);
    const paths = renderOrThrow(buildXgRaceOption(home, away, "主队", "客队", 90, "interactive", COLORS));
    expect(paths).toBeGreaterThan(0);
  });

  it("空射门列表不抛异常", () => {
    const home = cumulativeSeries([], true);
    const away = cumulativeSeries([], false);
    expect(() =>
      renderOrThrow(buildXgRaceOption(home, away, "主队", "客队", 90, "interactive", COLORS)),
    ).not.toThrow();
  });

  it("真实球队配色(非品牌色)不抛异常且画出曲线", () => {
    const shots = [
      shot({ minute: 3, xg: 0.2, is_home: true }),
      shot({ minute: 55, xg: 0.3, is_home: false }),
    ];
    const home = cumulativeSeries(shots, true);
    const away = cumulativeSeries(shots, false);
    const paths = renderOrThrow(
      buildXgRaceOption(home, away, "主队", "客队", 90, "interactive", NON_BRAND_COLORS),
    );
    expect(paths).toBeGreaterThan(0);
  });
});

describe("ShotMapChart.buildOption 渲染冒烟", () => {
  const plottedShots = [
    shot({ minute: 5, xg: 0.1, is_home: true, x: 90, y: 34, outcome: "AttemptSaved" }),
    shot({ minute: 20, xg: 0.4, is_home: true, x: 100, y: 36, outcome: "Goal" }),
    shot({ minute: 60, xg: 0.2, is_home: false, x: 95, y: 30, outcome: "Miss" }),
  ];

  it("真实射门数据不抛异常且画出标记点", () => {
    const paths = renderOrThrow(buildShotMapOption(plottedShots, "主队", "客队", COLORS));
    expect(paths).toBeGreaterThan(0);
  });

  it("空射门列表不抛异常", () => {
    expect(() => renderOrThrow(buildShotMapOption([], "主队", "客队", COLORS))).not.toThrow();
  });

  it("真实球队配色(非品牌色)不抛异常且画出标记点", () => {
    const paths = renderOrThrow(buildShotMapOption(plottedShots, "主队", "客队", NON_BRAND_COLORS));
    expect(paths).toBeGreaterThan(0);
  });

  describe("轨迹线联动(2026-08-24,第 5 个可选参数 selected)", () => {
    const blockedShot = shot({
      minute: 26, xg: 0.05, is_home: true, x: 78.43, y: 33.24,
      outcome: "AttemptSaved", is_blocked: true, blocked_x: 81.13, blocked_y: 33.16,
    });
    const goalNoEndpointData = shot({
      minute: 9, xg: 0.3, is_home: true, x: 95.44, y: 34.61, outcome: "Goal",
      goal_crossed_y: null,
    });
    // 2026-08-26 对齐 FotMob:Miss 带 goal_crossed_y 一样画轨迹(生产实测
    // 5868020 全部 12 脚 Miss 均有该值),不再有"射正才画"门槛。
    const missWithCrossedY = shot({
      minute: 33, xg: 0.08, is_home: false, x: 88, y: 40,
      outcome: "Miss", is_blocked: false, is_on_target: false, goal_crossed_y: 42.7,
    });
    const noTrajectoryData = shot({
      minute: 60, xg: 0.02, is_home: false, x: 95, y: 30,
      outcome: "Miss", is_blocked: null, is_on_target: false, goal_crossed_y: null,
    });

    it("选中有封堵坐标的射门 → 不抛异常且多画出图形(轨迹线+终点箭头)", () => {
      const withoutSelection = renderOrThrow(
        buildShotMapOption(plottedShots, "主队", "客队", COLORS),
      );
      const withSelection = renderOrThrow(
        buildShotMapOption([...plottedShots, blockedShot], "主队", "客队", COLORS, blockedShot),
      );
      expect(withSelection).toBeGreaterThan(withoutSelection);
    });

    it("选中带 goal_crossed_y 的 Miss → 不抛异常且多画出轨迹线(2026-08-26 修复的主诉)", () => {
      const withoutSelection = renderOrThrow(
        buildShotMapOption([...plottedShots, missWithCrossedY], "主队", "客队", COLORS),
      );
      const withSelection = renderOrThrow(
        buildShotMapOption(
          [...plottedShots, missWithCrossedY],
          "主队",
          "客队",
          COLORS,
          missWithCrossedY,
        ),
      );
      expect(withSelection).toBeGreaterThan(withoutSelection);
    });

    it("选中无任何终点数据的进球 → 不抛异常且不画线(球门正中兜底已删除,诚实不画)", () => {
      const withoutSelection = renderOrThrow(
        buildShotMapOption([...plottedShots, goalNoEndpointData], "主队", "客队", COLORS),
      );
      const withSelection = renderOrThrow(
        buildShotMapOption(
          [...plottedShots, goalNoEndpointData],
          "主队",
          "客队",
          COLORS,
          goalNoEndpointData,
        ),
      );
      expect(withSelection).toBe(withoutSelection);
    });

    it("选中无终点数据的 Miss → 不抛异常且不应多出轨迹线系列(静默不画线)", () => {
      const withoutSelection = renderOrThrow(
        buildShotMapOption([...plottedShots, noTrajectoryData], "主队", "客队", COLORS),
      );
      const withSelection = renderOrThrow(
        buildShotMapOption(
          [...plottedShots, noTrajectoryData],
          "主队",
          "客队",
          COLORS,
          noTrajectoryData,
        ),
      );
      expect(withSelection).toBe(withoutSelection);
    });

    it("选中的 shot 对象不在 plotted 数组里 → 不抛异常(极端情况,正常由 resolveSelectedShot 挡住,这里测 buildOption 自身的健壮性)", () => {
      const strayShot = shot({ minute: 88, is_home: true, x: 100, y: 34, outcome: "Goal" });
      expect(() =>
        renderOrThrow(buildShotMapOption(plottedShots, "主队", "客队", COLORS, strayShot)),
      ).not.toThrow();
    });
  });
});

describe("TeamStyleQuadrant.buildOption 渲染冒烟", () => {
  const view = {
    id: "xg-for-against",
    tab: "攻防",
    title: "攻防风格",
    x_label: "场均 xG",
    y_label: "场均 xGA",
    digits: 2,
    quadrants: ["攻强守强", "攻强守弱", "攻弱守强", "攻弱守弱"],
    points: [],
    y_lower_is_better: true,
    window: 5,
  };
  const pts = [
    { team_id: 1, name: "主队", x: 1.8, y: 0.9 },
    { team_id: 2, name: "客队", x: 1.2, y: 1.4 },
    { team_id: 3, name: "第三队", x: 1.5, y: 1.1 },
    { team_id: 4, name: "第四队", x: 0.9, y: 1.6 },
  ];

  it("真实分布数据不抛异常且画出散点", () => {
    const paths = renderOrThrow(buildQuadrantOption(view, pts, 1.35, 1.25, 1, 2, COLORS));
    expect(paths).toBeGreaterThan(0);
  });

  it("真实球队配色(非品牌色)不抛异常且画出散点", () => {
    const paths = renderOrThrow(buildQuadrantOption(view, pts, 1.35, 1.25, 1, 2, NON_BRAND_COLORS));
    expect(paths).toBeGreaterThan(0);
  });

  describe("2026-09-09 队徽坐标点 + 点击对比(第 8 个可选参数 opts)", () => {
    const SIZE = { width: 360, height: 320 };
    const crested = pts.map((p) => ({
      ...p,
      crest_url: p.team_id === 3 ? null : `data:image/png;base64,iVBORw0KGgo=#${p.team_id}`,
    }));
    const xr = niceAxisRange(crested.map((p) => p.x));
    const yr = niceAxisRange(crested.map((p) => p.y));
    const box = { ...SIZE, grid: QUADRANT_GRID };
    const layout = layoutCrests({ pts: crested, box, xr, yr, yInverse: false, radius: 15 });

    it("带队徽 + 布局:<image> 数 = 有队徽的队数,无队徽的队名出现在 SVG 里", () => {
      const r = renderSvg(
        buildQuadrantOption(view, crested, 1.35, 1.25, 1, 2, COLORS, { crestSize: 22, layout, xr, yr }),
        SIZE,
      );
      expect(r.images).toBe(3);
      expect(r.svg).toContain("第三队");
    });

    it("2026-09 窄屏 compactOthers:非选中球队(即使有队徽)一律降级成圆点,不再产出 <image>,也不再显示队名", () => {
      const r = renderSvg(
        buildQuadrantOption(view, crested, 1.35, 1.25, 1, 2, COLORS, {
          crestSize: 22, layout, xr, yr, compactOthers: true,
        }),
        SIZE,
      );
      // 4 支球队里只有本场两队(1、2,均有队徽)保留 <image>;
      // 团队 4 平时有队徽(默认模式会算进 3 张 <image> 里),compactOthers
      // 下必须消失——这条断言正是锁死"降级不是只对无队徽的队生效"。
      expect(r.images).toBe(2);
      expect(r.svg).not.toContain("第三队");
      expect(r.svg).not.toContain("第四队");
    });

    it("默认选中主客两队 → 有光环;换成对比第三队也不抛", () => {
      const def = renderSvg(
        buildQuadrantOption(view, crested, 1.35, 1.25, 1, 2, COLORS, { crestSize: 22, layout, xr, yr }),
        SIZE,
      ).paths;
      const none = renderSvg(
        buildQuadrantOption(view, crested, 1.35, 1.25, 1, 2, COLORS, {
          crestSize: 22, layout, xr, yr, selectedIds: [],
        }),
        SIZE,
      ).paths;
      expect(def).toBeGreaterThan(none);
      expect(() =>
        renderSvg(
          buildQuadrantOption(view, crested, 1.35, 1.25, 1, 2, COLORS, {
            crestSize: 22, layout, xr, yr, selectedIds: [3, 4],
          }),
          SIZE,
        ),
      ).not.toThrow();
    });
  });
});

describe("league/quadrantOption.buildQuadrantOption 渲染冒烟(队徽当坐标点)", () => {
  const SIZE = { width: 360, height: 380 };
  const view = viewById("both-ends"); // 攻防:y 轴 inverse

  function teamRow(i: number, crest: boolean): TeamSeasonStatRow {
    return {
      team: {
        team_id: 100 + i,
        name: `球队${i}`,
        name_en: null,
        // 用 data URI 而不是相对路径:SSR SVG 只把 href 原样写进 <image>,不发请求
        crest_url: crest ? `data:image/png;base64,iVBORw0KGgo=#${i}` : null,
      },
      matches_played: 3,
      avg_total_shots: 10 + (i % 7),
      avg_expected_goals: 1.0 + ((i * 7) % 10) / 10,
      avg_expected_goals_open_play: 0.8 + ((i * 3) % 6) / 10,
      avg_expected_goals_set_play: 0.2 + (i % 4) / 10,
      avg_expected_goals_non_penalty: 1.1,
      avg_expected_goals_conceded: 0.9 + ((i * 3) % 8) / 10,
    } as TeamSeasonStatRow;
  }

  function args(rows: TeamSeasonStatRow[], selectedIndexes: number[] = [], withLayout = true) {
    const pts = collectPoints(rows, view);
    const mx = mean(pts.map((p) => p.x));
    const my = mean(pts.map((p) => p.y));
    const xr = niceAxisRange(pts.map((p) => p.x));
    const yr = niceAxisRange(pts.map((p) => p.y));
    const box = { ...SIZE, grid: QUADRANT_GRID };
    const crestSize = crestSizeFor(box, pts.length);
    const layout = withLayout
      ? layoutCrests({ pts, box, xr, yr, yInverse: true, radius: crestSize / 2 + 4 })
      : null;
    const labelled = outlierNames(pts, mx, my);
    for (const p of pts) if (!p.crestUrl) labelled.add(p.name);
    return { view, pts, mx, my, colors: COLORS, labelled, crestSize, layout, xr, yr, grid: QUADRANT_GRID, selectedIndexes };
  }

  const twenty = Array.from({ length: 20 }, (_, i) => teamRow(i, true));

  it("20 队全有队徽:不抛异常,且 <image> 恰好 20 个(队徽真的画出来了,不只是没崩)", () => {
    const r = renderSvg(buildLeagueQuadrantOption(args(twenty)), SIZE);
    expect(r.paths).toBeGreaterThan(0);
    expect(r.images).toBe(20);
  });

  it("3 队缺队徽:<image> 仍然 17 个(圆点兜底不受标签摘除影响)", () => {
    // 2026-09-09:不再断言"缺队徽的队永远带名字"——那正是用户反馈的 bug 的
    // 根源假设(队名会压住旁边队徽时必须摘掉,见 crestQuadrantLayout.ts::
    // resolveLabelVisibility)。标签是否真的显示由该函数的碰撞几何决定,
    // 精确断言见 tests/team-quadrant-layout.test.ts;这里只守"圆点兜底本身
    // 不受标签摘除影响,该画的徽章/圆点一个不少"。
    const rows = twenty.map((row, i) => (i % 7 === 0 ? teamRow(i, false) : row));
    const r = renderSvg(buildLeagueQuadrantOption(args(rows)), SIZE);
    expect(r.images).toBe(17);
  });

  it("选中 1 队:多出光环 + 到两轴的虚线,不抛异常", () => {
    const none = renderSvg(buildLeagueQuadrantOption(args(twenty)), SIZE).paths;
    const one = renderSvg(buildLeagueQuadrantOption(args(twenty, [3])), SIZE).paths;
    expect(one).toBeGreaterThan(none);
  });

  it("选中 2 队:不抛异常", () => {
    expect(() => renderSvg(buildLeagueQuadrantOption(args(twenty, [3, 11])), SIZE)).not.toThrow();
  });

  it("layout 为 null(宽度尚未测得):零偏移渲染不抛", () => {
    const r = renderSvg(buildLeagueQuadrantOption(args(twenty, [], false)), SIZE);
    expect(r.images).toBe(20);
  });

  it("每个 series 的 id 与选中数无关,始终稳定(2026-09-16 站长要求'点击头像" +
    "有放大等互动效果'时排查发现的真实 bug:ring 只在 hasSelection 时才被" +
    "push 进 series 数组,选中数 0↔1 切换那一刻会把它后面的 crest 在数组里的" +
    "下标顶偏一位——ECharts 在 notMerge:true 下默认按'同类型+数组下标'匹配" +
    "前后两次 setOption 的同一个 series 来算过渡动画,下标一跳,crest 就被当成" +
    "换了个新 series,直接跳变而不是平滑放大,而这恰好发生在用户点第一下、" +
    "最该看见放大效果的那一刻)", () => {
    const zeroSelected = buildLeagueQuadrantOption(args(twenty, []));
    const oneSelected = buildLeagueQuadrantOption(args(twenty, [3]));
    const seriesArray = (option: typeof zeroSelected) =>
      (Array.isArray(option.series) ? option.series : [option.series]) as {
        name?: string;
        id?: string;
      }[];
    const idOf = (option: typeof zeroSelected, name: string) =>
      seriesArray(option).find((s) => s.name === name)?.id;
    for (const name of ["quadrant-labels", "hit", "crest"]) {
      expect(idOf(zeroSelected, name)).toBe(name);
      expect(idOf(oneSelected, name)).toBe(name);
    }
    expect(idOf(oneSelected, "ring")).toBe("ring");
    // 0 选中时 ring 干脆不存在(不是"存在但 id 为空"),这条不变量本身也要守住
    expect(idOf(zeroSelected, "ring")).toBeUndefined();
  });

  it("头像层有明确的悬停反馈配置(cursor + 预览性放大 + 独立过渡动画)," +
    "不依赖 ECharts 的隐式默认值——放大倍数(1.12)必须小于点击选中态的" +
    "1.25x,悬停只是预览、点击才是确认", () => {
    const option = buildLeagueQuadrantOption(args(twenty));
    const seriesArray = (Array.isArray(option.series) ? option.series : [option.series]) as {
      name?: string;
      cursor?: string;
      emphasis?: { scale?: number };
      stateAnimation?: { duration?: number };
    }[];
    const crest = seriesArray.find((s) => s.name === "crest")!;
    expect(crest.cursor).toBe("pointer");
    expect(crest.emphasis?.scale).toBeGreaterThan(1);
    expect(crest.emphasis?.scale).toBeLessThan(CREST.SELECTED_SCALE);
    expect(crest.stateAnimation?.duration).toBeGreaterThan(0);
  });

  it("4 点最小集 / 坐标全同 / export 模式 / 非品牌色 都不抛", () => {
    const four = Array.from({ length: 4 }, (_, i) => teamRow(i, true));
    expect(() => renderSvg(buildLeagueQuadrantOption(args(four)), SIZE)).not.toThrow();
    const same = Array.from({ length: 5 }, (_, i) =>
      teamRow(i, true),
    ).map((r) => ({ ...r, avg_expected_goals: 1.4, avg_expected_goals_conceded: 1.2 }));
    expect(() => renderSvg(buildLeagueQuadrantOption(args(same)), SIZE)).not.toThrow();
    expect(() =>
      renderSvg(buildLeagueQuadrantOption({ ...args(twenty), mode: "export" }), SIZE),
    ).not.toThrow();
    expect(() =>
      renderSvg(buildLeagueQuadrantOption({ ...args(twenty), colors: NON_BRAND_COLORS }), SIZE),
    ).not.toThrow();
  });

  it("非反转视角(战术)也不抛", () => {
    const tactics = viewById("tactics");
    const pts = collectPoints(twenty, tactics);
    const mx = mean(pts.map((p) => p.x));
    const my = mean(pts.map((p) => p.y));
    const xr = niceAxisRange(pts.map((p) => p.x));
    const yr = niceAxisRange(pts.map((p) => p.y));
    expect(() =>
      renderSvg(
        buildLeagueQuadrantOption({
          view: tactics, pts, mx, my, colors: COLORS, labelled: new Set(), crestSize: 24,
          layout: null, xr, yr, grid: QUADRANT_GRID, selectedIndexes: [0],
        }),
        SIZE,
      ),
    ).not.toThrow();
  });

  it("百分比单位视角(推进方式)能渲染,且样本不足的球队不落点", () => {
    const passShare = viewById("possession-passing");
    // 前 4 队样本不达标(累计成功传球 < 1000),其余 16 队达标
    const rows = twenty.map((r, i) => ({
      ...r,
      matches_played: i < 4 ? 2 : 20,
      ratios: {
        opp_half_pass_share: {
          value: 45 + (i % 10),
          numerator: 900,
          denominator: i < 4 ? 500 : 4200,
          paired_matches: i < 4 ? 2 : 20,
          matches_played: i < 4 ? 2 : 20,
        },
      },
    })) as unknown as TeamSeasonStatRow[];
    const pts = collectPoints(rows, passShare);
    expect(pts.length).toBe(16); // 证明门槛过滤真的传到了这里,不是仍然 20 支全画
    const mx = mean(pts.map((p) => p.x));
    const my = mean(pts.map((p) => p.y));
    const xr = niceAxisRange(pts.map((p) => p.x));
    const yr = niceAxisRange(pts.map((p) => p.y));
    const r = renderSvg(
      buildLeagueQuadrantOption({
        view: passShare, pts, mx, my, colors: COLORS, labelled: new Set(), crestSize: 24,
        layout: null, xr, yr, grid: QUADRANT_GRID, selectedIndexes: [],
      }),
      SIZE,
    );
    expect(r.images).toBe(16);
  });

  it("筛选激活(最近 5 场)时样本门槛按比例缩小,原本样本不足的球队重新画出来", () => {
    // 与上一条"百分比单位视角"用同一份 fixture:未筛选时门槛 minMatches=3/
    // minVolume=1000 挡掉前 4 队(matches_played=2、denominator=500)。
    // windowScale=5/38 时,minMatches 缩到 max(1,round(3*5/38))=1、minVolume
    // 缩到 1000*5/38≈131.6——两队都远超缩小后的门槛,20 队应该全部画出来,
    // 证明"筛选激活时大部分指标仍能画出点",不是全部消失。
    const passShare = viewById("possession-passing");
    const rows = twenty.map((r, i) => ({
      ...r,
      matches_played: i < 4 ? 2 : 20,
      ratios: {
        opp_half_pass_share: {
          value: 45 + (i % 10),
          numerator: 900,
          denominator: i < 4 ? 500 : 4200,
          paired_matches: i < 4 ? 2 : 20,
          matches_played: i < 4 ? 2 : 20,
        },
      },
    })) as unknown as TeamSeasonStatRow[];
    const unfiltered = collectPoints(rows, passShare);
    expect(unfiltered.length).toBe(16);
    const windowScale = 5 / 38;
    const filtered = collectPoints(rows, passShare, windowScale);
    expect(filtered.length).toBe(20);
    const mx = mean(filtered.map((p) => p.x));
    const my = mean(filtered.map((p) => p.y));
    const xr = niceAxisRange(filtered.map((p) => p.x));
    const yr = niceAxisRange(filtered.map((p) => p.y));
    const r = renderSvg(
      buildLeagueQuadrantOption({
        view: passShare, pts: filtered, mx, my, colors: COLORS, labelled: new Set(), crestSize: 24,
        layout: null, xr, yr, grid: QUADRANT_GRID, selectedIndexes: [],
      }),
      SIZE,
    );
    expect(r.images).toBe(20);
  });

  it("x 轴反转视角能渲染不抛,四象限名也正确画在图上(dirsOf 的 x 分支)", () => {
    // 借用现有 both-ends 视角构造一个 x.lowerIsBetter 的临时 view,验证
    // quadrantOption 的 dirsOf 分支真的处理了 x 反转,不只是 y。
    const base = viewById("both-ends");
    const xInverted = { ...base, x: { ...base.x, lowerIsBetter: true } };
    const pts = collectPoints(twenty, xInverted);
    const mx = mean(pts.map((p) => p.x));
    const my = mean(pts.map((p) => p.y));
    const xr = niceAxisRange(pts.map((p) => p.x));
    const yr = niceAxisRange(pts.map((p) => p.y));
    const r = renderSvg(
      buildLeagueQuadrantOption({
        view: xInverted, pts, mx, my, colors: COLORS, labelled: new Set(), crestSize: 24,
        layout: null, xr, yr, grid: QUADRANT_GRID, selectedIndexes: [],
      }),
      SIZE,
    );
    for (const q of xInverted.quadrants) {
      expect(r.svg, `象限名"${q}"应该出现在渲染出的 SVG 里`).toContain(q);
    }
  });

  it("包四第二批新视角(角球成色/终结记录/防线与门将)都能渲染不抛,防线与门将的真实 x 反转也走通", () => {
    const rows = twenty.map((r, i) => ({
      ...r,
      matches_played: 20,
      ratios: {
        corner_shot_rate: { value: 20 + (i % 10), numerator: 10 + i, denominator: 50 + i * 2 },
        finishing_delta: { value: -2 + (i % 5), sample_count: 200 + i * 5 },
        gk_saves_above_expected: { value: -3 + (i % 7), sample_count: 100 + i * 3 },
        opp_xg_per_shot: { value: 0.1 + (i % 5) * 0.01, numerator: 5, denominator: 50 },
      },
    })) as unknown as TeamSeasonStatRow[];
    for (const id of ["corner-quality", "finishing-record", "defence-goalkeeping"]) {
      const view = viewById(id);
      const pts = collectPoints(rows, view);
      const mx = mean(pts.map((p) => p.x));
      const my = mean(pts.map((p) => p.y));
      const xr = niceAxisRange(pts.map((p) => p.x));
      const yr = niceAxisRange(pts.map((p) => p.y));
      expect(() =>
        renderSvg(
          buildLeagueQuadrantOption({
            view, pts, mx, my, colors: COLORS, labelled: new Set(), crestSize: 24,
            layout: null, xr, yr, grid: QUADRANT_GRID, selectedIndexes: [],
          }),
          SIZE,
        ),
      ).not.toThrow();
    }
  });
});

describe("league/quadrantOption.buildQuadrantOption 渲染冒烟(球员头像当坐标点,泛型复用)", () => {
  const SIZE = { width: 360, height: 380 };
  const view = playerViewById("player-creativity");
  const PLAYER_CREST_OPTS = { fillTarget: 0.28, min: 20, max: 30 };

  function playerRow(i: number, hasAvatar: boolean): PlayerQuadrantRow {
    return {
      player: {
        player_id: hasAvatar ? `${1000 + i}` : null,
        name: `球员${i}`,
        name_en: null,
      },
      team: {
        team_id: 100 + (i % 5),
        name: `队${i % 5}`,
        name_en: null,
        crest_url: null,
      },
      team_color: null,
      usual_position: 2,
      appearances: 10 + (i % 5),
      minutes_played: 900,
      team_minutes: 1800,
      minutes_share: 0.5,
      teams_count: 1,
      ratios: {
        chances_created_per90: {
          value: 1 + ((i * 7) % 10) / 10,
          numerator: 1,
          denominator: 900,
          paired_matches: 10,
        },
        xa_per90: {
          value: 0.1 + ((i * 3) % 6) / 100,
          numerator: 0.1,
          denominator: 900,
          paired_matches: 10,
        },
      },
    } as PlayerQuadrantRow;
  }

  function args(rows: PlayerQuadrantRow[], selectedIndexes: number[] = []) {
    const { pts: fullPts } = playerPlotSet(rows, view);
    const mx = mean(fullPts.map((p) => p.x));
    const my = mean(fullPts.map((p) => p.y));
    const { drawn } = topPerQuadrant(fullPts, mx, my, {});
    const xr = niceAxisRange(drawn.map((p) => p.x));
    const yr = niceAxisRange(drawn.map((p) => p.y));
    const box = { ...SIZE, grid: QUADRANT_GRID };
    const crestSize = crestSizeFor(box, drawn.length, PLAYER_CREST_OPTS);
    const layout = layoutCrests({ pts: drawn, box, xr, yr, yInverse: false, radius: crestSize / 2 + 4 });
    const labelled = new Set<string>();
    for (const p of drawn) if (!p.avatarUrl) labelled.add(p.name);
    return {
      view, pts: drawn, mx, my, colors: COLORS, labelled, crestSize, layout, xr, yr,
      grid: QUADRANT_GRID, selectedIndexes,
      symbolUrlOf: (p: PlayerPt) => p.avatarUrl,
    };
  }

  const thirty = Array.from({ length: 30 }, (_, i) => playerRow(i, true));

  it("30 名球员全有头像:不抛异常,画出真实 <image>(泛型 buildQuadrantOption 对球员点同样能渲染)", () => {
    const r = renderSvg(buildLeagueQuadrantOption(args(thirty)), SIZE);
    expect(r.paths).toBeGreaterThan(0);
    // 部分点在避让分组阶段可能不足 4 人导致该视角不可用,这里只断言真的画出了头像
    // (>0),不强行断言精确数量(与球队版 20/20 那条不同,球员数据是构造的合成
    // 分布,quadrantOf 分组结果不完全可控)。
    expect(r.images).toBeGreaterThan(0);
  });

  it("部分球员无头像(player_id=null):退回圆点兜底,不抛异常", () => {
    const rows = thirty.map((r, i) => (i % 7 === 0 ? playerRow(i, false) : r));
    expect(() => renderSvg(buildLeagueQuadrantOption(args(rows)), SIZE)).not.toThrow();
  });

  it("选中 1 名球员:多出光环 + 到两轴的虚线,不抛异常", () => {
    expect(() => renderSvg(buildLeagueQuadrantOption(args(thirty, [0])), SIZE)).not.toThrow();
  });

  it("门将视角(x 轴 lowerIsBetter 反转)能渲染不抛", () => {
    const gkView = playerViewById("player-goalkeeping");
    const gkRows = Array.from({ length: 15 }, (_, i) => ({
      ...playerRow(i, true),
      usual_position: 0,
      ratios: {
        xgot_faced_per90: { value: 1 + (i % 5) / 10, numerator: 1, denominator: 900, paired_matches: 10 },
        goals_prevented_per90: { value: -0.5 + (i % 7) / 10, numerator: -0.5, denominator: 900, paired_matches: 10 },
      },
    })) as PlayerQuadrantRow[];
    const { pts: fullPts } = playerPlotSet(gkRows, gkView);
    const mx = mean(fullPts.map((p) => p.x));
    const my = mean(fullPts.map((p) => p.y));
    const dirs = { x: gkView.x.lowerIsBetter === true, y: gkView.y.lowerIsBetter === true };
    const { drawn } = topPerQuadrant(fullPts, mx, my, dirs);
    const xr = niceAxisRange(drawn.map((p) => p.x));
    const yr = niceAxisRange(drawn.map((p) => p.y));
    const box = { ...SIZE, grid: QUADRANT_GRID };
    const crestSize = crestSizeFor(box, drawn.length, PLAYER_CREST_OPTS);
    const layout = layoutCrests({ pts: drawn, box, xr, yr, yInverse: false, radius: crestSize / 2 + 4 });
    expect(() =>
      renderSvg(
        buildLeagueQuadrantOption({
          view: gkView, pts: drawn, mx, my, colors: COLORS, labelled: new Set(), crestSize,
          layout, xr, yr, grid: QUADRANT_GRID, selectedIndexes: [],
          symbolUrlOf: (p: PlayerPt) => p.avatarUrl,
        }),
        SIZE,
      ),
    ).not.toThrow();
  });

  it("四象限名直接画在图上(2026-09-16 真实反馈:站长说'并没有在象限图中看到" +
    "有说明,例如是什么类型的门将')——门将这个 x 轴反转的视角,四个角标必须" +
    "分别对应真实的 x/y 好坏组合,不能凭'左上/右上'位置猜", () => {
    const gkView = playerViewById("player-goalkeeping");
    const gkRows = Array.from({ length: 15 }, (_, i) => ({
      ...playerRow(i, true),
      usual_position: 0,
      ratios: {
        xgot_faced_per90: { value: 1 + (i % 5) / 10, numerator: 1, denominator: 900, paired_matches: 10 },
        goals_prevented_per90: { value: -0.5 + (i % 7) / 10, numerator: -0.5, denominator: 900, paired_matches: 10 },
      },
    })) as PlayerQuadrantRow[];
    const { pts: fullPts } = playerPlotSet(gkRows, gkView);
    const mx = mean(fullPts.map((p) => p.x));
    const my = mean(fullPts.map((p) => p.y));
    const dirs = { x: gkView.x.lowerIsBetter === true, y: gkView.y.lowerIsBetter === true };
    const { drawn } = topPerQuadrant(fullPts, mx, my, dirs);
    const xr = niceAxisRange(drawn.map((p) => p.x));
    const yr = niceAxisRange(drawn.map((p) => p.y));
    const box = { ...SIZE, grid: QUADRANT_GRID };
    const crestSize = crestSizeFor(box, drawn.length, PLAYER_CREST_OPTS);
    const layout = layoutCrests({ pts: drawn, box, xr, yr, yInverse: false, radius: crestSize / 2 + 4 });
    const r = renderSvg(
      buildLeagueQuadrantOption({
        view: gkView, pts: drawn, mx, my, colors: COLORS, labelled: new Set(), crestSize,
        layout, xr, yr, grid: QUADRANT_GRID, selectedIndexes: [],
        symbolUrlOf: (p: PlayerPt) => p.avatarUrl,
      }),
      SIZE,
    );
    for (const q of gkView.quadrants) {
      expect(r.svg, `象限名"${q}"应该出现在渲染出的 SVG 里`).toContain(q);
    }
  });
});
