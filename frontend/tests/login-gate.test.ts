/**
 * 登录门禁的前端侧(2026-10-02,经站长批准):未登录只能看英超。
 *
 * - lib/login-gate.ts::ANON_LEAGUE_IDS 是后端 backend/queries/leagues.py::
 *   ANON_LEAGUE_IDS 的镜像,TS 不能 import Python 字典,这里读后端源码比对;
 * - sitemap / llms.txt 只列未登录可浏览的联赛(其余联赛对爬虫只是登录跳转壳);
 * - 模拟器页服务端只能给匿名口径的真实比赛索引(盘口线是赔率数据)。
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { GET } from "@/app/llms.txt/route";
import sitemap from "@/app/sitemap";
import { fixtureIndex } from "@/app/simulator/loadParams";
import type { SimParams } from "@/features/simulator/types";
import { ANON_LEAGUE_IDS, isAnonLeague, loginHref } from "@/lib/login-gate";

describe("ANON_LEAGUE_IDS 与后端单一真源一致", () => {
  it("与 backend/queries/leagues.py 的 ANON_LEAGUE_IDS 相同", () => {
    const src = readFileSync(join(__dirname, "../../backend/queries/leagues.py"), "utf-8");
    const m = src.match(/ANON_LEAGUE_IDS[^=]*=\s*frozenset\(\{([^}]*)\}\)/);
    expect(m, "未能在后端源码里找到 ANON_LEAGUE_IDS").not.toBeNull();
    const backend = m![1].split(",").map((x) => Number(x.trim())).filter(Number.isFinite).sort();
    expect([...ANON_LEAGUE_IDS].sort()).toEqual(backend);
  });

  it("isAnonLeague 接受数字与字符串", () => {
    expect(isAnonLeague(47)).toBe(true);
    expect(isAnonLeague("47")).toBe(true);
    expect(isAnonLeague(87)).toBe(false);
  });

  it("loginHref 把当前路径编码进 next", () => {
    expect(loginHref("/matches/1?from=/matches?date=2026-10-02")).toBe(
      "/login?next=%2Fmatches%2F1%3Ffrom%3D%2Fmatches%3Fdate%3D2026-10-02",
    );
  });
});

describe("sitemap / llms.txt 只列未登录可浏览的联赛", () => {
  it("sitemap 的联赛页只有英超", () => {
    const leagueUrls = sitemap()
      .map((e) => e.url)
      .filter((u) => u.includes("/league/"));
    expect(leagueUrls.length).toBeGreaterThan(0);
    for (const u of leagueUrls) expect(u).toMatch(/\/league\/47\//);
  });

  it("llms.txt 只列英超联赛页", async () => {
    const text = await GET().text();
    const ids = [...text.matchAll(/\/league\/(\d+)\//g)].map((m) => Number(m[1]));
    expect(ids.length).toBeGreaterThan(0);
    expect(new Set(ids)).toEqual(new Set([47]));
  });
});

describe("模拟器真实比赛索引按联赛过滤", () => {
  const p = {
    teams: { "1": { name_zh: "甲" }, "2": { name_zh: "乙" }, "3": { name_zh: "丙" }, "4": { name_zh: "丁" } },
    fixtures: {
      "10": { match_id: 10, league_id: 47, home_team_id: 1, away_team_id: 2, kickoff_at_utc: "2026-10-03T19:00:00Z", status: "未开赛", final_score: null, ah: { line: 0.5 }, ou: null, market_lambda: { home: 1, away: 1 } },
      "11": { match_id: 11, league_id: 87, home_team_id: 3, away_team_id: 4, kickoff_at_utc: "2026-10-03T19:00:00Z", status: "未开赛", final_score: null, ah: { line: -0.25 }, ou: null, market_lambda: { home: 1, away: 1 } },
    },
  } as unknown as SimParams;

  it("给定 ANON_LEAGUE_IDS 时只剩英超;不给时五大联赛全部", () => {
    expect(fixtureIndex(p, ANON_LEAGUE_IDS).map((f) => f.match_id)).toEqual([10]);
    expect(fixtureIndex(p).map((f) => f.match_id)).toEqual([10, 11]);
  });
});
