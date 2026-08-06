/**
 * 比赛链接收口测试(2026-08-06 审计 B5:联赛赛程 12,726 行展示位全是死路;
 * returnTo 白名单只认 /matches,联赛页/战绩页的返回上下文被静默丢弃)。
 */

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  buildMatchHref,
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
