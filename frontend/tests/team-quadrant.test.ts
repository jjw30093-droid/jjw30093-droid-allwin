import { describe, expect, it } from "vitest";
import {
  collectPoints,
  outlierNames,
  quadrantOf,
  type __TestView,
} from "@/components/league/TeamQuadrantChart";
import type { TeamSeasonStatRow } from "@/lib/api-v1";

function row(
  name: string,
  over: Partial<TeamSeasonStatRow> = {},
): TeamSeasonStatRow {
  return {
    team: { id: 1, name, badge_url: null },
    matches_played: 38,
    avg_total_shots: 12,
    avg_shots_on_target: 4,
    avg_possession: 50,
    avg_expected_goals: 1.4,
    avg_expected_goals_on_target: 1.3,
    avg_expected_goals_open_play: 1.0,
    avg_expected_goals_set_play: 0.3,
    avg_expected_goals_non_penalty: 1.3,
    avg_expected_goals_conceded: 1.2,
    ...over,
  } as TeamSeasonStatRow;
}

const attackDefence: __TestView = {
  x: { key: "avg_expected_goals" },
  y: { key: "avg_expected_goals_conceded" },
};

describe("球队象限图取数", () => {
  it("缺任一坐标的球队被丢弃,而不是补 0", () => {
    // 0 在 xG 语境里是真实值("一次机会都没创造"),不能当缺失占位 ——
    // 若补 0,缺 xGA 的球队会被画成"防守全联赛最好",是彻底的假信息。
    const rows = [
      row("有数据"),
      row("缺被创造 xG", { avg_expected_goals_conceded: null }),
      row("真的是 0", { avg_expected_goals_conceded: 0 }),
    ];
    const pts = collectPoints(rows, attackDefence);
    expect(pts.map((p) => p.name)).toEqual(["有数据", "真的是 0"]);
    expect(pts.find((p) => p.name === "真的是 0")?.y).toBe(0);
  });

  it("默认(y 越高越好)时象限按数值高低切分", () => {
    const mk = (x: number, y: number) => ({ name: "t", x, y, mp: 38 });
    expect(quadrantOf(mk(2, 2), 1, 1)).toBe(0); // 双好
    expect(quadrantOf(mk(0, 2), 1, 1)).toBe(1); // x 差 y 好
    expect(quadrantOf(mk(0, 0), 1, 1)).toBe(2); // 双差
    expect(quadrantOf(mk(2, 0), 1, 1)).toBe(3); // x 好 y 差
  });

  it("y 越低越好时象限翻转 —— 攻守兼备不能被标成对攻型", () => {
    // 每场被创造 xG 低 = 防守好。若不传 lowerIsBetter,
    // "创造多 + 被创造少"会落进索引 3(对攻型),与配色互相打架。
    const mk = (x: number, y: number) => ({ name: "t", x, y, mp: 38 });
    expect(quadrantOf(mk(2, 0), 1, 1, true)).toBe(0); // 攻强守也强
    expect(quadrantOf(mk(0, 0), 1, 1, true)).toBe(1); // 攻弱守强
    expect(quadrantOf(mk(0, 2), 1, 1, true)).toBe(2); // 两头都弱
    expect(quadrantOf(mk(2, 2), 1, 1, true)).toBe(3); // 对攻型
    // 同一个点在两种口径下必须落到不同象限,否则说明 flag 根本没生效
    expect(quadrantOf(mk(2, 0), 1, 1, true)).not.toBe(quadrantOf(mk(2, 0), 1, 1));
  });

  it("正好等于均值的点算作好侧,不会漏画", () => {
    expect(quadrantOf({ name: "t", x: 1, y: 1, mp: null }, 1, 1)).toBe(0);
    expect(quadrantOf({ name: "t", x: 1, y: 1, mp: null }, 1, 1, true)).toBe(3);
  });

  it("战术视角取运动战/定位球两列", () => {
    const pts = collectPoints(
      [row("A", { avg_expected_goals_open_play: 1.1, avg_expected_goals_set_play: 0.5 })],
      { x: { key: "avg_expected_goals_open_play" }, y: { key: "avg_expected_goals_set_play" } },
    );
    expect(pts[0]).toMatchObject({ x: 1.1, y: 0.5 });
  });

  it("标注球队按标准差归一化挑选,量纲大的轴不会独吞名额", () => {
    // x 量纲 ~12(射门数)、y 量纲 ~1.4(xG)。若不归一化,x 上偏离 1.0
    // 会盖过 y 上偏离 0.5 —— 而按各自标准差算,后者才是真正的异常值。
    const pts = [
      { name: "x 偏离一点点", x: 13, y: 1.4, mp: 38 },
      { name: "y 极端", x: 12, y: 2.4, mp: 38 },
      { name: "普通A", x: 12, y: 1.4, mp: 38 },
      { name: "普通B", x: 12, y: 1.4, mp: 38 },
      { name: "普通C", x: 12, y: 1.4, mp: 38 },
    ];
    const picked = outlierNames(pts, 12.2, 1.6, 2);
    expect(picked.has("y 极端")).toBe(true);
    expect(picked.size).toBe(2);
  });

  it("球队数少于名额时不报错,全部标注", () => {
    const pts = [
      { name: "A", x: 1, y: 1, mp: 10 },
      { name: "B", x: 2, y: 2, mp: 10 },
    ];
    expect(outlierNames(pts, 1.5, 1.5, 6).size).toBe(2);
  });
});
