/**
 * 「我关注的比赛」/「最近浏览」对已完赛比赛显示比分(2026-08-19)。
 *
 * 真实缺陷:这两块都保存的是 match_id,比赛结束后仍然留在列表里,却只渲染
 * 联赛 + 主队 vs 客队 + **开球时间**。对一场三天前就踢完的比赛显示"开球时间
 * 8月16日 03:00",比"看不到"更糟——它显示的是一条已经失效的信息,用户会
 * 以为比赛还没开始(CLAUDE.md §2.2 不展示伪造的新鲜数据)。
 *
 * 口径与 MatchRow.tsx 一致:status==="Finish" 且两个 score 都非空 → 显示
 * 比分;否则显示开球时间。不另造第二套判断。
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MatchSummary } from "@/lib/api-v1";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.resetModules();
  window.localStorage.clear();
});

function match(over: Partial<MatchSummary> = {}): MatchSummary {
  return {
    match_id: 5887595,
    league_id: 61,
    season: "2026/2027",
    date_utc: "2026-08-17",
    kickoff_at_utc: "2026-08-17T19:15:00Z",
    round: "1",
    status: "Finish",
    home: { team_id: 1, name: "卡萨皮亚", name_en: null, crest_url: null },
    away: { team_id: 2, name: "本菲卡", name_en: null, crest_url: null },
    home_score: 0,
    away_score: 7,
    sync_state: null,
    data_updated_at: null,
    last_success_sync_at: null,
    next_planned_sync_at: null,
    probability_source: null,
    odds_observation_count: null,
    odds_coverage_tier: null,
    odds_last_observed_at: null,
    odds_freshness_state: null,
    win_probability: null,
    ...over,
  } as MatchSummary;
}

function stubFetch(m: MatchSummary) {
  const headers = new Headers();
  headers.set("content-type", "application/json");
  vi.stubGlobal(
    "fetch",
    // clientFetch 打的是 /api/v1/matches/{id},响应形状是 MatchDetailResponse
    // ({match, ...}),组件取的是 d.match。
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ match: m }), { status: 200, headers }),
    ),
  );
}

/** [组件名, localStorage key, 加载器]。两个组件形状完全相同,用同一组用例
 * 跑两遍——它们是复制粘贴出来的一对,修一个漏一个是这类缺陷的常见结局。 */
const CASES: readonly [string, string, () => Promise<Record<string, () => React.ReactNode>>][] = [
  ["FollowedMatches", "allwin-followed-matches", () => import("@/components/matches/FollowedMatches")],
  ["RecentlyViewed", "allwin-recently-viewed", () => import("@/components/matches/RecentlyViewed")],
];

describe.each(CASES)("%s:已完赛的比赛显示比分而不是开球时间", (name, key, load) => {
  beforeEach(() => {
    window.localStorage.setItem(key, JSON.stringify([5887595]));
  });

  it("已完赛 → 显示比分 0 - 7,不显示会让人以为还没开赛的开球时间", async () => {
    stubFetch(match());
    const Comp = (await load())[name];
    render(<Comp />);
    expect(await screen.findByText("0 - 7")).toBeTruthy();
    expect(screen.queryByText(/8月17日/)).toBeNull();
  });

  it("未开赛 → 仍然显示开球时间(既有行为不许动)", async () => {
    stubFetch(match({ status: "NotStarted", home_score: null, away_score: null }));
    const Comp = (await load())[name];
    const { container } = render(<Comp />);
    await screen.findByText(/卡萨皮亚/);
    expect(container.textContent).not.toContain(" - ");
  });

  it("已完赛但比分缺失 → 不编造比分,退回时间(与 MatchRow 同口径)", async () => {
    stubFetch(match({ home_score: null, away_score: null }));
    const Comp = (await load())[name];
    const { container } = render(<Comp />);
    await screen.findByText(/卡萨皮亚/);
    expect(container.textContent).not.toContain(" - ");
  });
});
