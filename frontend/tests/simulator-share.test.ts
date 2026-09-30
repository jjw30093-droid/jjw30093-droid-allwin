// @vitest-environment node
import { describe, expect, it } from "vitest";
import { contrastRatioHex } from "@/components/charts/colorContrast";
import { buildShareUrl, decodeResult, encodeResult, parseSetupQuery, resultToken, setupQuery, sharedSetupOf } from "@/features/simulator/shareLink";
import { shareTeamColors } from "@/features/simulator/shareImage";
import type { ResultSnapshot } from "@/features/simulator/snapshot";

const POS = [11, 32, 34, 36, 38, 64, 66, 83, 85, 87, 115];

function snap(): ResultSnapshot {
  const team = (id: number, name: string, prefix: string) => ({
    teamId: id,
    name,
    formation: "4-2-3-1",
    focuses: id === 9825 ? (["setpiece", "press"] as const).slice() : [],
    shortRest: id !== 9825,
    lineup: POS.map((p, i) => ({ positionId: p, group: "CM" as const, playerId: `${prefix}${1000 + i}`, name: `球员${i}`, num: String(i + 1), x: 0.5, y: 0.1 * i })),
    expectedGoals: 1.5,
  });
  return {
    sv: 1,
    modelVersion: "v0.3",
    paramsDate: "2026-09-29T23:58:41Z",
    leagueId: 47,
    seed: 20260929,
    chaos: false,
    kappa: 0,
    fixtureId: null,
    market: null,
    teams: [team(9825, "阿森纳", "1"), team(8456, "曼城", "2")],
    single: {
      seed: 20260929,
      score: [1, 2],
      events: [{ tick: 30, minute: 31, added: 0, clock: "31'", team: 1, kind: "goal", channel: "setpiece", playerId: "21000", playerName: "格埃希", xg: 0.1, isGoal: true, score: [0, 1] }],
      totalTicks: 99,
      halfTimeTick: 47,
      stoppage: [2, 7],
      epsilon: [1, 1],
    },
    many: {
      runs: 1000, pHome: 0.472, pDraw: 0.287, pAway: 0.241, topScores: [{ score: "1-1", p: 0.13 }], meanGoals: [1.5, 1.2],
      fairAhLine: 0.5, fairOuLine: 2.75, crown: null, upset: { scoreCount: 50, outcomeShare: 0.241, outcome: "A" },
      scorerProb: [{ team: 1, playerId: "21000", name: "格埃希", p: 0.1 }], pBigMargin: 0.15, meanReds: 0.2, meanPenalties: 0.3, goalBuckets: [0.3, 0.3, 0.4, 0.4, 0.4, 0.6],
    },
  } as ResultSnapshot;
}

describe("分享链接:设定写在查询参数里", () => {
  it("往返一致:阵型、首发、侧重点、休息、随机强度、种子、模型版本、参数日期", () => {
    const s = sharedSetupOf(snap());
    const parsed = parseSetupQuery(`?${setupQuery(s)}`);
    expect(parsed).toEqual(s);
  });
  it("不合法的设定整体拒绝(首发不是 11 人、未知侧重点、阵型格式、球员 id 字符)", () => {
    const q = new URLSearchParams(setupQuery(sharedSetupOf(snap())));
    const bad = (k: string, v: string) => {
      const x = new URLSearchParams(q);
      x.set(k, v);
      return parseSetupQuery(`?${x}`);
    };
    expect(bad("hx", q.get("hx")!.split(",").slice(0, 10).join(","))).toBeNull();
    expect(bad("hk", "setpiece.teleport")).toBeNull();
    expect(bad("af", "<script>")).toBeNull();
    expect(bad("ax", q.get("ax")!.replace("21000", "21000<b>"))).toBeNull();
  });
});

describe("分享链接:本次结果压缩后写在 # 片段里", () => {
  it("压缩 → 解压原样还原", async () => {
    const token = await encodeResult(snap());
    expect(token[0]).toBe("z");
    expect(await decodeResult(token)).toEqual(snap());
  });
  it("未压缩兜底格式也能解析;损坏的片段返回 null", async () => {
    const json = Buffer.from(JSON.stringify(snap())).toString("base64url");
    expect(await decodeResult(`j${json}`)).toEqual(snap());
    expect(await decodeResult("zAAAA")).toBeNull();
    expect(await decodeResult(`j${Buffer.from('{"sv":2}').toString("base64url")}`)).toBeNull();
  });
  it("完整链接:? 设定 + #r= 结果", async () => {
    const url = await buildShareUrl("http://localhost:3020/simulator", snap());
    const u = new URL(url);
    expect(parseSetupQuery(u.search)?.seed).toBe(20260929);
    expect(await decodeResult(resultToken(u.hash)!)).toEqual(snap());
  });
});

describe("分享图队色", () => {
  it("落在白色卡片上的对比度 ≥ 3:1;不合法或救不回来的颜色回退兜底色", () => {
    for (const c of shareTeamColors(["#ffe066", "#f5f5f5"])) expect(contrastRatioHex(c, "#ffffff")).toBeGreaterThanOrEqual(3);
    expect(shareTeamColors(["#087e78", "not-a-color"])).toEqual(["#087e78", "#b45309"]);
  });
});
