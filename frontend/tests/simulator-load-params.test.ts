// @vitest-environment node
import { mkdtemp, rm, symlink, utimes, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fixtureIndex, isStale, leagueOf, loadParams, sliceForLeague } from "@/app/simulator/loadParams";
import type { SimParams } from "@/features/simulator/types";

function params(generatedAt: string): SimParams {
  const team = (id: number, lg: number, prefix: string) => ({
    team_id: id,
    league_id: lg,
    name_zh: `队${id}`,
    squad: [`${prefix}1`, `${prefix}2`],
    last_lineup: { match_id: 1, date: "2026-09-20", formation: "4-3-3", starters: [{ player_id: `${prefix}1`, position_id: 11 }] },
  });
  return {
    meta: { generated_at: generatedAt, model_version: "v0.3", effective_version: "v0.3", uncalibrated: true },
    leagues: { "47": {}, "87": {}, "55": {}, "54": {}, "53": {} },
    formations: {},
    position_map: { "4-3-3": { "11": "GK" } },
    teams: { "1": team(1, 47, "a"), "2": team(2, 47, "b"), "3": team(3, 87, "c") },
    players: { a1: {}, a2: {}, b1: {}, b2: {}, c1: {}, c2: {} },
    fixtures: {
      "10": { match_id: 10, league_id: 47, home_team_id: 1, away_team_id: 2, kickoff_at_utc: "2026-10-01T19:00:00Z", status: "未开赛", final_score: null, ah: { line: 0.5 }, ou: null, market_lambda: { home: 1.5, away: 1 } },
      "11": { match_id: 11, league_id: 87, home_team_id: 3, away_team_id: 3, kickoff_at_utc: "2026-10-01T19:00:00Z", status: "未开赛", final_score: null, ah: null, ou: null, market_lambda: null },
    },
    calibration: {},
  } as unknown as SimParams;
}

let dir = "";
beforeEach(async () => {
  dir = await mkdtemp(join(tmpdir(), "simparams-"));
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(async () => {
  vi.restoreAllMocks();
  await rm(dir, { recursive: true, force: true });
});

describe("参数读取:current.json → 按日期回退 → 不可用", () => {
  it("读 current.json(软链)", async () => {
    await writeFile(join(dir, "simulator_params_20260930.json"), JSON.stringify(params("2026-09-30T00:00:00Z")));
    await symlink("simulator_params_20260930.json", join(dir, "current.json"));
    const r = await loadParams({ dir });
    expect(r?.source).toBe("current.json");
    expect(r?.fallback).toBe(false);
  });

  it("current.json 损坏时回退到最新一份可用的日期文件,不抛异常", async () => {
    await writeFile(join(dir, "simulator_params_20260928.json"), JSON.stringify(params("2026-09-28T00:00:00Z")));
    await writeFile(join(dir, "simulator_params_20260929.json"), "{ 截断的 JSON");
    await writeFile(join(dir, "current.json"), "{}");
    const r = await loadParams({ dir });
    expect(r?.source).toBe("simulator_params_20260928.json");
    expect(r?.fallback).toBe(true);
  });

  it("什么都读不到时返回 null(页面显示'参数暂不可用')", async () => {
    expect(await loadParams({ dir })).toBeNull();
    expect(await loadParams({ dir: join(dir, "不存在") })).toBeNull();
    expect(await loadParams({})).toBeNull();
  });

  it("文件被替换(mtime 变化)后读到新内容", async () => {
    const f = join(dir, "p.json");
    await writeFile(f, JSON.stringify(params("2026-09-29T00:00:00Z")));
    expect((await loadParams({ path: f }))?.params.meta.generated_at).toBe("2026-09-29T00:00:00Z");
    await writeFile(f, JSON.stringify(params("2026-09-30T00:00:00Z")));
    await utimes(f, new Date(), new Date(Date.now() + 5000));
    expect((await loadParams({ path: f }))?.params.meta.generated_at).toBe("2026-09-30T00:00:00Z");
  });
});

describe("按联赛下发", () => {
  const p = params("2026-09-30T00:00:00Z");
  it("只保留当前联赛的球队、球员、赛程", () => {
    const s = sliceForLeague(p, 47);
    expect(Object.keys(s.teams).sort()).toEqual(["1", "2"]);
    expect(Object.keys(s.players).sort()).toEqual(["a1", "a2", "b1", "b2"]);
    expect(Object.keys(s.fixtures)).toEqual(["10"]);
    expect(Object.keys(s.leagues).length).toBe(5);
  });
  it("lg 不在五大联赛时回到英超", () => {
    expect(leagueOf("87", p)).toBe(87);
    expect(leagueOf("42", p)).toBe(47);
    expect(leagueOf(undefined, p)).toBe(47);
    expect(leagueOf(["55"], p)).toBe(55);
  });
  it("真实比赛小索引只含有 Crown 反推 λ 的比赛,带中文队名", () => {
    expect(fixtureIndex(p)).toEqual([
      expect.objectContaining({ match_id: 10, home_name: "队1", away_name: "队2", ah_line: 0.5, ou_line: null }),
    ]);
  });
  it("超过 48 小时未更新视为不是最新", () => {
    expect(isStale(p, Date.parse("2026-10-01T23:00:00Z"))).toBe(false);
    expect(isStale(p, Date.parse("2026-10-02T01:00:00Z"))).toBe(true);
  });
});
