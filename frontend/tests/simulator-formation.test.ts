import { describe, expect, it } from "vitest";
import type { TeamSetup } from "@/features/simulator/engine";
import { applyDrop, formationChoices, lastLineupSetup, reassignFormation } from "@/features/simulator/formation";
import type { PlayerParams, PosGroup, SimParams } from "@/features/simulator/types";

// 两套阵型:模板坐标 + position_map 分组(与导出同形)
const F442: [number, PosGroup, number, number][] = [
  [11, "GK", 0.5, 0.1], [32, "FB", 0.12, 0.36], [34, "CB", 0.38, 0.36], [36, "CB", 0.62, 0.36], [38, "FB", 0.88, 0.36],
  [72, "W", 0.12, 0.61], [74, "CM", 0.38, 0.61], [76, "CM", 0.62, 0.61], [78, "W", 0.88, 0.61], [104, "ST", 0.3, 0.87], [106, "ST", 0.7, 0.87],
];
const F433: [number, PosGroup, number, number][] = [
  [11, "GK", 0.5, 0.1], [32, "FB", 0.12, 0.36], [34, "CB", 0.38, 0.36], [36, "CB", 0.62, 0.36], [38, "FB", 0.88, 0.36],
  [73, "CM", 0.21, 0.61], [75, "CM", 0.5, 0.61], [77, "CM", 0.79, 0.61], [103, "W", 0.21, 0.87], [105, "ST", 0.5, 0.87], [107, "W", 0.79, 0.87],
];

function player(id: string, pos: PosGroup): PlayerParams {
  return { player_id: id, name_zh: id, name_en: null, main_position: pos, minutes: 900, shirt_number: 1, a_p: 0.1, r_p: 5, g_p: pos === "GK" ? 0 : null, penalties_taken: 0 } as unknown as PlayerParams;
}

function params(): SimParams {
  const players: Record<string, PlayerParams> = {};
  F442.forEach(([, g], i) => (players[`p${i}`] = player(`p${i}`, g)));
  const tpl = (f: typeof F442) => ({ samples: 10, slots: f.map(([pid, , x, y]) => ({ position_id: pid, x, y })) });
  const map = (f: typeof F442) => Object.fromEntries(f.map(([pid, g]) => [String(pid), g]));
  return {
    formations: { "4-4-2": tpl(F442), "4-3-3": tpl(F433), "3-4-1-2": { samples: 1, slots: tpl(F433).slots.slice(0, 10) } },
    position_map: { "4-4-2": map(F442), "4-3-3": map(F433), "3-4-1-2": map(F433) },
    players,
    teams: {
      "10": {
        team_id: 10, league_id: 1, name_zh: "甲队", squad: Object.keys(players),
        last_lineup: { match_id: 1, date: "2026-09-19", formation: "4-4-2", starters: F442.map(([pid], i) => ({ player_id: `p${i}`, position_id: pid })) },
      },
    },
  } as unknown as SimParams;
}

describe("阵型选择与切换", () => {
  it("可选阵型:模板 11 个槽位且每个槽位都在 position_map 里;必选阵型排在前面", () => {
    expect(formationChoices(params())).toEqual(["4-3-3", "4-4-2"]);
  });

  it("4-4-2 → 4-3-3:不换人,门将留门将,同组优先,左右不交叉;分组来自 position_map", () => {
    const p = params();
    const before = lastLineupSetup(p, 10);
    const after = reassignFormation(p, before, "4-3-3")!;
    expect(after.formation).toBe("4-3-3");
    expect(after.slots.map((s) => s.playerId).sort()).toEqual(before.slots.map((s) => s.playerId).sort());
    const at = (pid: number) => after.slots.find((s) => s.positionId === pid)!;
    expect(at(11).playerId).toBe("p0");
    expect([at(32).playerId, at(34).playerId, at(36).playerId, at(38).playerId]).toEqual(["p1", "p2", "p3", "p4"]);
    expect(at(103).playerId).toBe("p5"); // 左边锋留在左边
    expect(at(107).playerId).toBe("p8"); // 右边锋留在右边
    expect(new Set([at(73).playerId, at(75).playerId, at(77).playerId])).toContain("p6");
    expect(new Set([at(73).playerId, at(75).playerId, at(77).playerId])).toContain("p7");
    expect(["p9", "p10"]).toContain(at(105).playerId);
    for (const s of after.slots) expect(s.group).toBe(p.position_map["4-3-3"][String(s.positionId)]);
  });

  it("相邻关系:没有同组槽位时落到相邻组(后腰 → 中场),而不是其他组", () => {
    const p = params();
    p.position_map["4-4-2"]["74"] = "DM";
    const after = reassignFormation(p, lastLineupSetup(p, 10), "4-3-3")!;
    const slotOfP6 = after.slots.find((s) => s.playerId === "p6")!;
    expect(slotOfP6.group).toBe("CM");
  });

  it("恢复最近一场首发保留侧重点与休息设定", () => {
    const s = lastLineupSetup(params(), 10, { focuses: ["setpiece"], shortRest: true });
    expect(s.formation).toBe("4-4-2");
    expect(s.focuses).toEqual(["setpiece"]);
    expect(s.shortRest).toBe(true);
    expect(s.slots[0].playerId).toBe("p0");
  });
});

describe("拖放落位", () => {
  const base = (): TeamSetup => lastLineupSetup(params(), 10);
  it("阵容池球员拖到位置上:换上,原球员回到阵容池", () => {
    const next = applyDrop(base(), "pool:x9", "slot:3")!;
    expect(next.slots[3].playerId).toBe("x9");
    expect(next.slots.some((s) => s.playerId === "p3")).toBe(false);
  });
  it("两名首发互换", () => {
    const next = applyDrop(base(), "slot:9", "slot:5")!;
    expect(next.slots[9].playerId).toBe("p5");
    expect(next.slots[5].playerId).toBe("p9");
  });
  it("落在空白处、拖到自己身上、已在首发里的球员:不变", () => {
    expect(applyDrop(base(), "slot:2", null)).toBeNull();
    expect(applyDrop(base(), "slot:2", "slot:2")).toBeNull();
    const b = base();
    expect(applyDrop(b, "pool:p4", "slot:1")).toBe(b);
  });
});
