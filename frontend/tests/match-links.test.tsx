/**
 * 比赛链接收口测试(2026-08-06 审计 B5:联赛赛程 12,726 行展示位全是死路;
 * returnTo 白名单只认 /matches,联赛页/战绩页的返回上下文被静默丢弃)。
 */

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  buildMatchHref,
  relatedMatchesQuery,
  returnLabelFor,
  sanitizeReturnTo,
} from "@/lib/match-links";
import { FixtureRounds } from "@/components/league/FixtureRounds";
import type { MatchSummary } from "@/lib/api-v1";

afterEach(cleanup);

describe("buildMatchHref", () => {
  it("无 returnTo 时输出裸路径", () => {
    expect(buildMatchHref(123)).toBe("/matches/123");
  });
  it("returnTo 编码进 from 查询参数", () => {
    expect(buildMatchHref(123, "/league/87/matches?season=2020/2021")).toBe(
      "/matches/123?from=%2Fleague%2F87%2Fmatches%3Fseason%3D2020%2F2021",
    );
  });
});

describe("sanitizeReturnTo(开放重定向防护)", () => {
  it("接受站内列表页前缀", () => {
    expect(sanitizeReturnTo("/matches?league=47")).toBe("/matches?league=47");
    expect(sanitizeReturnTo("/league/87/matches?season=2020/2021")).toBe(
      "/league/87/matches?season=2020/2021",
    );
    expect(sanitizeReturnTo("/track-record")).toBe("/track-record");
    expect(sanitizeReturnTo("/")).toBe("/");
  });
  it("拒绝协议相对/带协议/反斜杠变体", () => {
    expect(sanitizeReturnTo("//evil.example")).toBe("/matches");
    expect(sanitizeReturnTo("https://evil.example")).toBe("/matches");
    expect(sanitizeReturnTo("/\\evil")).toBe("/matches");
    expect(sanitizeReturnTo("javascript:alert(1)")).toBe("/matches");
  });
  it("未知前缀回退 /matches", () => {
    expect(sanitizeReturnTo("/admin")).toBe("/matches");
    expect(sanitizeReturnTo(undefined)).toBe("/matches");
  });
});

describe("returnLabelFor(返回文案按来源)", () => {
  it("联赛页来源显示「返回赛程」", () => {
    expect(returnLabelFor("/league/87/matches?season=2020/2021")).toBe("返回赛程");
  });
  it("战绩页来源显示「返回公开战绩」", () => {
    expect(returnLabelFor("/track-record")).toBe("返回公开战绩");
  });
  it("默认「返回当前筛选结果」", () => {
    expect(returnLabelFor("/matches?league=47")).toBe("返回当前筛选结果");
  });
});

function sampleMatch(overrides: Partial<MatchSummary> = {}): MatchSummary {
  return {
    match_id: 3900933,
    league_id: 47,
    season: "2022/2023",
    date_utc: "2022-08-06",
    kickoff_at_utc: "2022-08-06T19:30:00Z",
    round: "1",
    status: "Finish",
    home: { team_id: 9879, name: "富勒姆", name_en: "Fulham", crest_url: null },
    away: { team_id: 8650, name: "利物浦", name_en: "Liverpool", crest_url: null },
    home_score: 2,
    away_score: 2,
    ...overrides,
  } as MatchSummary;
}

describe("FixtureRounds 行是真实链接(B5 直接回归)", () => {
  it("每行渲染 <a href='/matches/{id}?from=…'>", () => {
    const { container } = render(
      <FixtureRounds
        matches={[sampleMatch()]}
        returnTo="/league/47/matches?season=2022/2023"
      />,
    );
    const anchor = container.querySelector("a");
    expect(anchor).not.toBeNull();
    expect(anchor?.getAttribute("href")).toBe(
      "/matches/3900933?from=%2Fleague%2F47%2Fmatches%3Fseason%3D2022%2F2023",
    );
    expect(anchor?.textContent).toContain("富勒姆");
    expect(anchor?.textContent).toContain("2 - 2");
  });
});

/**
 * 详情页"上一场/下一场"取数收口(2026-08-19,详情页性能修复)。
 *
 * 真实缺陷:app/matches/[matchId]/page.tsx 和 MemberMatchDetail.tsx(SSR 与
 * 客户端兜底两份独立取数)各自拼了一遍 `/api/v1/matches?league_id=X&
 * status=upcoming&window=7d&limit=200`——整页 200 场比赛的 payload 最终只
 * 产出两个 match_id(上一场/下一场链接),且两处拼接逻辑一旦分叉就会出现
 * SSR 与客户端兜底给出不同导航结果的漂移(这类"不允许出现第二套取数逻辑"
 * 的纪律与 frontend/lib/match-filters.ts 的既有先例一致)。
 *
 * 收口到一个函数,把 limit 从 200 降到刚好够覆盖"同联赛一周内相邻比赛"的
 * 量级——不新建后端接口(避免复刻 list_matches 的优先级排序语义,那部分
 * 排序不是纯按开球时间,贸然简化会悄悄改变"下一场"点进去是哪一场),
 * status/window/排序全部沿用既有 /api/v1/matches 契约,只收窄 limit。
 */
describe("relatedMatchesQuery:上一场/下一场取数收口", () => {
  it("拼出的 query 串带 status=upcoming、window=7d、正确的 league_id,且 limit 远小于 200", () => {
    const qs = relatedMatchesQuery(47);
    const params = new URLSearchParams(qs);
    expect(params.get("league_id")).toBe("47");
    expect(params.get("status")).toBe("upcoming");
    expect(params.get("window")).toBe("7d");
    const limit = Number(params.get("limit"));
    expect(limit).toBeGreaterThan(0);
    expect(limit).toBeLessThan(200);
  });

  it("同一个 league_id 每次拼出完全相同的串(SSR 与客户端必须拼出同一个 URL)", () => {
    expect(relatedMatchesQuery(87)).toBe(relatedMatchesQuery(87));
  });

  it("不同联赛拼出不同的 league_id", () => {
    expect(relatedMatchesQuery(47)).not.toBe(relatedMatchesQuery(87));
  });
});
