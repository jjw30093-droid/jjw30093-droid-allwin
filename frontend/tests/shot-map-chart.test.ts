import { describe, expect, it } from "vitest";
import { filterShots } from "@/components/matches/ShotMapChart";
import type { MatchReportResponse } from "@/lib/api-v1";

type MatchReport = Extract<MatchReportResponse, { available: true }>;
type Shot = MatchReport["shots"][number];

function shot(overrides: Partial<Shot>): Shot {
  return {
    player_id: "p1",
    player_name: "球员",
    team_id: 1,
    is_home: true,
    minute: 10,
    period: "FirstHalf",
    x: 90,
    y: 34,
    xg: 0.3,
    xgot: null,
    situation: "RegularPlay",
    outcome: "Goal",
    shot_type: "RightFoot",
    is_blocked: null,
    is_on_target: null,
    ...overrides,
  };
}

const BASE_FILTER = { side: "both" as const, outcome: "on_target" as const, situations: [], bodyPart: null, half: "all" as const };

describe("filterShots 射正筛选(2026-08-23 起 is_blocked 已知时精确排除被封堵的球)", () => {
  it("is_blocked=true 的 AttemptSaved 不算射正,is_blocked=false 的算", () => {
    const shots = [
      shot({ outcome: "AttemptSaved", is_blocked: false }),
      shot({ outcome: "AttemptSaved", is_blocked: true }),
      shot({ outcome: "Goal", is_blocked: false }),
    ];
    const result = filterShots(shots, BASE_FILTER);
    expect(result).toHaveLength(2);
    expect(result.every((s) => !(s.outcome === "AttemptSaved" && s.is_blocked))).toBe(true);
  });

  it("is_blocked 未回填(null)时退回旧口径,不排除任何 AttemptSaved", () => {
    const shots = [
      shot({ outcome: "AttemptSaved", is_blocked: null }),
      shot({ outcome: "Miss" }),
    ];
    const result = filterShots(shots, BASE_FILTER);
    expect(result).toHaveLength(1);
    expect(result[0].outcome).toBe("AttemptSaved");
  });

  it("Miss/Post 从不算射正,不论 is_blocked", () => {
    const shots = [shot({ outcome: "Miss", is_blocked: false }), shot({ outcome: "Post" })];
    expect(filterShots(shots, BASE_FILTER)).toHaveLength(0);
  });
});
