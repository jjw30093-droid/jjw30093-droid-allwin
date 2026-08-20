/**
 * MatchRow 渲染测试。
 *
 * 2026-08-20:sync_state/odds_coverage_tier 两行内部运维口径提示(此前分别
 * 经历过"UNAVAILABLE 被静默吞掉""过期数据被写死成完整走势"两次真实 bug
 * 修复)已按站长要求整行删除——两句话字面上容易读成互相矛盾,对普通用户
 * 没有实际信息量。本文件保留权限口径(requires_login 已删除字段)与"两行
 * 提示彻底不再渲染"两组测试,历史 bug 相关的具体文案断言已随功能一起移除。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MatchRow } from "@/components/matches/MatchRow";
import type { MatchSummary } from "@/lib/api-v1";

afterEach(() => {
  cleanup();
});

function baseMatch(overrides: Partial<MatchSummary> = {}): MatchSummary {
  return {
    match_id: 1,
    league_id: 1,
    season: "2025-2026",
    date_utc: "2026-08-20",
    kickoff_at_utc: "2026-08-20T19:00:00Z",
    round: null,
    status: "NotStarted",
    home: { team_id: 10, name: "主队FC", name_en: null, crest_url: null },
    away: { team_id: 20, name: "客队FC", name_en: null, crest_url: null },
    home_score: null,
    away_score: null,
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
    ...overrides,
  } as MatchSummary;
}

const WIN_PROBABILITY = {
  p_home: 0.48,
  p_draw: 0.27,
  p_away: 0.25,
  observed_at: "2026-08-20T09:00:00Z",
};

describe("MatchRow 权限口径修正(2026-08-16):requires_login 字段已从后端删除", () => {
  it("给定真实 win_probability 数据(不含 requires_login 字段)时完整渲染概率条,不出现任何登录/锁定文案", () => {
    render(<MatchRow match={baseMatch({ win_probability: WIN_PROBABILITY })} />);
    expect(screen.getByText("主胜")).not.toBeNull();
    expect(screen.queryByText(/登录/)).toBeNull();
    expect(screen.queryByText(/解锁/)).toBeNull();
    expect(screen.queryByText(/锁定/)).toBeNull();
    expect(screen.queryByText(/Premium/i)).toBeNull();
  });

  it("即使响应体里意外还带着已删除的 requires_login=true(遗留数据/旧缓存),也绝不渲染登录提示——这是死代码,不是运行时判断", () => {
    // MatchSummary 类型已不再声明 requires_login,这里用 as unknown 强制构造
    // 一个"万一"还带着这个字段的对象,证明组件真的不再读取它(不是仅仅因为
    // fixture 没设置这个字段、恰好落到 falsy 分支才没渲染)。
    const legacyMatch = {
      ...baseMatch({ win_probability: null }),
      requires_login: true,
    } as unknown as MatchSummary;
    render(<MatchRow match={legacyMatch} />);
    expect(screen.queryByText("登录后查看胜平负概率")).toBeNull();
  });
});

describe("MatchRow 不再渲染 sync_state/odds_coverage_tier 提示行(2026-08-20 站长要求整行删除)", () => {
  // 此前这里有两组测试分别守着 sync_state("部分数据暂不可用"等)与
  // odds_coverage_tier("赔率:完整走势"等)两行文案的具体渲染规则——两句话
  // 字面上容易读成互相矛盾,对普通用户没有实际信息量,已被整行删除
  // (不是重新措辞/合并,是彻底不再渲染),测试改为断言"这些内部口径字段
  // 不管取什么值都不应该出现任何相关文字",防止将来被误加回来。
  it("sync_state 无论 FRESH/STALE/UNAVAILABLE,都不渲染任何相关提示文字", () => {
    for (const state of ["FRESH", "STALE", "UNAVAILABLE"] as const) {
      const { unmount } = render(
        <MatchRow
          match={baseMatch({
            sync_state: state,
            data_updated_at: "2026-08-19T10:00:00Z",
            next_planned_sync_at: "2026-08-20T18:00:00Z",
          })}
        />,
      );
      expect(screen.queryByText("数据已更新")).toBeNull();
      expect(screen.queryByText("数据等待刷新")).toBeNull();
      expect(screen.queryByText("部分数据暂不可用")).toBeNull();
      unmount();
    }
  });

  it("odds_coverage_tier 无论哪个档位,都不渲染任何「赔率:」文案", () => {
    for (const tier of ["full_timeline", "open_close_only"] as const) {
      const { unmount } = render(
        <MatchRow
          match={baseMatch({
            odds_coverage_tier: tier,
            odds_freshness_state: "FRESH",
          })}
        />,
      );
      expect(screen.queryByText(/^赔率:/)).toBeNull();
      unmount();
    }
  });
});
