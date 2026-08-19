/**
 * 首页重点卡的「焦点战」标签(2026-08-19,配合 24h 窗口 + 强强对话优先)。
 *
 * 排序规则把强强对话顶到重点位之后,卡面上必须有一句话说明"为什么是这场",
 * 否则用户只看到一张普通比赛卡,规则等于没生效。判据复用
 * lib/homepage.ts::marqueeRank(同一份 team_id 名单),不在组件里写第二套。
 *
 * §11.2 约束(样式在 app/page.module.css::.pairMarqueeBadge 里验收,jsdom
 * 测不了真实计算样式):不用红色(红=真实错误)、字号 12px 落在 DESIGN.md
 * 字号阶梯上、卡头最多"联赛·轮次 + 焦点战 + 比赛状态"三层,不堆胶囊墙。
 */

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HomeMatchExperienceLive } from "@/components/home/HomeMatchExperienceLive";
import type { MatchSummary } from "@/lib/api-v1";
import type { HomeMatchCard } from "@/lib/homepage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function stubFetchRejecting() {
  // 挂载后的客户端刷新在测试里静默失败并保留传入的 initial* props——这是组件
  // 既有的降级设计,这里只是不让它发起真实网络请求。
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.reject(new Error("no network in test"))),
  );
}

function card(homeTeamId: number, awayTeamId: number): HomeMatchCard {
  return {
    match: {
      match_id: 777,
      league_id: 47,
      season: "2025-2026",
      date_utc: "2026-08-20",
      kickoff_at_utc: "2026-08-20T19:00:00Z",
      round: "3",
      status: "NotStarted",
      home: { team_id: homeTeamId, name: "主队FC", name_en: null, crest_url: null },
      away: { team_id: awayTeamId, name: "客队FC", name_en: null, crest_url: null },
      home_score: null,
      away_score: null,
      win_probability: null,
    } as MatchSummary,
    tip: null,
  };
}

function renderFeatured(featured: HomeMatchCard) {
  stubFetchRejecting();
  render(
    <HomeMatchExperienceLive
      initialFeatured={featured}
      initialSecondary={[]}
      initialCounts={null}
      initialFreshness={null}
      initialErrored={false}
    />,
  );
  return screen.getByTestId("featured-match-card");
}

describe("重点卡「焦点战」标签", () => {
  it("强强对话(阿森纳 vs 切尔西)必须打出焦点战标签", () => {
    const featured = renderFeatured(card(9825, 8455));
    expect(within(featured).getByText("焦点战")).toBeTruthy();
  });

  it("普通比赛不打焦点战标签(不是每张卡都挂,否则等于没有)", () => {
    const featured = renderFeatured(card(9825, 8322)); // 阿森纳 vs 非 Big6
    expect(within(featured).queryByText("焦点战")).toBeNull();
  });

  it("team_id 缺失时不打标签也不崩溃", () => {
    stubFetchRejecting();
    const missing = card(9825, 8455);
    (missing.match.home as { team_id: number | null }).team_id = null;
    expect(() =>
      render(
        <HomeMatchExperienceLive
          initialFeatured={missing}
          initialSecondary={[]}
          initialCounts={null}
          initialFreshness={null}
          initialErrored={false}
        />,
      ),
    ).not.toThrow();
    expect(
      within(screen.getByTestId("featured-match-card")).queryByText("焦点战"),
    ).toBeNull();
  });

  it("卡头状态元素不超过三层(§11.2 不堆胶囊墙)", () => {
    const featured = renderFeatured(card(8633, 8634));
    const header = featured.querySelector("header");
    expect(header).not.toBeNull();
    // 联赛·轮次 / 焦点战 / 比赛状态 —— 直接子元素恰好两块:联赛行 + 徽章组,
    // 徽章组里最多两枚。
    const badgeGroup = header!.lastElementChild;
    expect(badgeGroup!.children.length).toBeLessThanOrEqual(2);
    expect(within(featured).getByText("焦点战")).toBeTruthy();
    expect(within(featured).getByText("未开赛")).toBeTruthy();
  });
});
