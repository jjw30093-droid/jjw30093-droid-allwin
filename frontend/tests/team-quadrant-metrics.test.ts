/**
 * 象限图详情面板取数(2026-09-09):排名、均值、派生指标、选中态纯逻辑。
 * 面板上每个数字都从这里出,所以这里断言"随输入变化"与"缺失不补 0"。
 */

import { describe, expect, it } from "vitest";
import type { TeamSeasonStatRow } from "@/lib/api-v1";
import {
  METRICS,
  columnMetric,
  formatMetric,
  leagueMean,
  rankOf,
  teamKey,
} from "@/components/league/teamMetrics";
import {
  VIEWS,
  axisToMetric,
  collectPoints,
  resolveClickedKey,
  resolveSelectedTeams,
  toggleSelection,
} from "@/components/league/quadrantViews";

function row(id: number, name: string, over: Partial<TeamSeasonStatRow> = {}): TeamSeasonStatRow {
  return {
    team: { team_id: id, name, name_en: null, crest_url: `/api/v1/media/team-crests/fotmob/${id}.png` },
    matches_played: 3,
    avg_total_shots: 12,
    avg_expected_goals: 1.4,
    avg_expected_goals_non_penalty: 1.3,
    avg_expected_goals_conceded: 1.2,
    ...over,
  } as TeamSeasonStatRow;
}

const xg = columnMetric("avg_expected_goals", { label: "xG", unit: "", digits: 2 });
const xga = columnMetric("avg_expected_goals_conceded", {
  label: "xGA",
  unit: "",
  digits: 2,
  lowerIsBetter: true,
});

describe("rankOf", () => {
  const rows = [
    row(1, "A", { avg_expected_goals: 2.0, avg_expected_goals_conceded: 0.8 }),
    row(2, "B", { avg_expected_goals: 1.5, avg_expected_goals_conceded: 1.5 }),
    row(3, "C", { avg_expected_goals: 1.5, avg_expected_goals_conceded: 1.0 }),
    row(4, "D", { avg_expected_goals: 1.0, avg_expected_goals_conceded: null }),
  ];

  it("越高越好:并列同名次(1224 制)", () => {
    expect(rankOf(rows, xg, teamKey({ team_id: 1, name: "A" }))).toEqual({ rank: 1, total: 4 });
    expect(rankOf(rows, xg, teamKey({ team_id: 2, name: "B" }))).toEqual({ rank: 2, total: 4 });
    expect(rankOf(rows, xg, teamKey({ team_id: 3, name: "C" }))).toEqual({ rank: 2, total: 4 });
    expect(rankOf(rows, xg, teamKey({ team_id: 4, name: "D" }))).toEqual({ rank: 4, total: 4 });
  });

  it("lowerIsBetter 升序,且缺值的队不进分母", () => {
    expect(rankOf(rows, xga, teamKey({ team_id: 1, name: "A" }))).toEqual({ rank: 1, total: 3 });
    expect(rankOf(rows, xga, teamKey({ team_id: 2, name: "B" }))).toEqual({ rank: 3, total: 3 });
  });

  it("目标球队自身缺值 → null,不是排最后", () => {
    expect(rankOf(rows, xga, teamKey({ team_id: 4, name: "D" }))).toBeNull();
  });
});

describe("leagueMean 与派生指标", () => {
  it("分母只数有值的队;全联赛缺失 → null", () => {
    const rows = [row(1, "A", { clean_sheets: 5 }), row(2, "B"), row(3, "C", { clean_sheets: 1 })];
    expect(leagueMean(rows, METRICS.cleanSheets)).toEqual({ mean: 3, n: 2 });
    expect(leagueMean(rows, METRICS.corners)).toBeNull();
  });

  it("点球 xG = 总 xG − 非点球 xG;任一缺失 → null 不补 0", () => {
    expect(METRICS.penXg.value(row(1, "A", { avg_expected_goals: 1.5, avg_expected_goals_non_penalty: 1.2 }))).toBeCloseTo(0.3);
    expect(METRICS.penXg.value(row(1, "A", { avg_expected_goals_non_penalty: null }))).toBeNull();
  });

  it("每脚射门 xG = xG ÷ 射门数;射门为 0 或缺失 → null", () => {
    expect(METRICS.xgPerShot.value(row(1, "A", { avg_expected_goals: 1.2, avg_total_shots: 12 }))).toBeCloseTo(0.1);
    expect(METRICS.xgPerShot.value(row(1, "A", { avg_total_shots: 0 }))).toBeNull();
    expect(METRICS.xgPerShot.value(row(1, "A", { avg_total_shots: null }))).toBeNull();
  });

  it("formatMetric:缺值显示 —,有值带单位", () => {
    expect(formatMetric(null, xg)).toBe("—");
    expect(formatMetric(12.34, columnMetric("avg_total_shots", { label: "", unit: "脚", digits: 1 }))).toBe("12.3脚");
  });

  it("axisToMetric 保留 lowerIsBetter 方向", () => {
    const m = axisToMetric(VIEWS[0].y);
    expect(m.lowerIsBetter).toBe(true);
    expect(m.value(row(1, "A", { avg_expected_goals_conceded: 0.7 }))).toBe(0.7);
  });
});

describe("选中态纯逻辑", () => {
  it("toggleSelection:切换 / 上限 2 / 满员 FIFO", () => {
    expect(toggleSelection([], "a")).toEqual(["a"]);
    expect(toggleSelection(["a"], "a")).toEqual([]);
    expect(toggleSelection(["a"], "b")).toEqual(["a", "b"]);
    expect(toggleSelection(["a", "b"], "c")).toEqual(["b", "c"]);
    expect(toggleSelection(["a", "b"], "a")).toEqual(["b"]);
  });

  it("resolveSelectedTeams 挡陈旧选中并保持选中顺序", () => {
    const pts = collectPoints(
      [row(1, "A"), row(2, "B"), row(3, "C")],
      { x: { key: "avg_expected_goals" }, y: { key: "avg_expected_goals_conceded" } },
    );
    const keyB = teamKey({ team_id: 2, name: "B" });
    const keyA = teamKey({ team_id: 1, name: "A" });
    const gone = teamKey({ team_id: 99, name: "Z" });
    expect(resolveSelectedTeams(pts, [keyB, gone, keyA]).map((p) => p.name)).toEqual(["B", "A"]);
  });

  it("collectPoints 同一支球队多行只画一次(key 唯一,选中与 React key 才不打架)", () => {
    const pts = collectPoints([row(1, "A"), row(1, "A"), row(2, "B")], VIEWS[0]);
    expect(pts.map((p) => p.key)).toEqual(["id:1", "id:2"]);
  });

  it("collectPoints 带出 key / crestUrl / teamId", () => {
    const pts = collectPoints([row(7, "七")], VIEWS[0]);
    expect(pts[0]).toMatchObject({
      key: "id:7",
      crestUrl: "/api/v1/media/team-crests/fotmob/7.png",
      teamId: 7,
    });
  });

  it("resolveClickedKey 优先 dataIndex,payload 兜底,点空白为 null", () => {
    const pts = collectPoints([row(1, "A"), row(2, "B")], VIEWS[0]);
    expect(resolveClickedKey({ seriesName: "crest", dataIndex: 1 }, pts)).toBe("id:2");
    expect(resolveClickedKey({ seriesName: "hit", dataIndex: 0 }, pts)).toBe("id:1");
    // 克隆后的 payload(引用不等)也能解析
    expect(resolveClickedKey({ seriesName: "other", data: { pt: { key: "id:2" } } }, pts)).toBe("id:2");
    expect(resolveClickedKey({}, pts)).toBeNull();
    expect(resolveClickedKey(null, pts)).toBeNull();
  });
});
