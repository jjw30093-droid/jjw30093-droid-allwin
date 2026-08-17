/**
 * MatchRow 渲染测试(用户用真实数据复现:95/95 场 upcoming 比赛的
 * sync_state=UNAVAILABLE,列表上却完全看不到任何提示——第77行的条件
 * 只判断了 "STALE",从未处理 "UNAVAILABLE",UNAVAILABLE 被静默吞掉)。
 *
 * 关键断言:
 * - sync_state="UNAVAILABLE" 必须渲染出可见文字提示(不是空白);
 * - sync_state="STALE" 的原有提示行为不能回归;
 * - odds_coverage_tier="full_timeline" 且 odds_freshness_state="STALE" 时,
 *   不能无条件宣称"完整走势"——必须带过期限定语(不能出现裸的"完整走势");
 * - odds_coverage_tier="full_timeline" 且 odds_freshness_state="FRESH" 时,
 *   "完整走势"文案照常显示(不回归)。
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

describe("MatchRow sync_state 提示(bug1:UNAVAILABLE 曾被静默吞掉)", () => {
  it('sync_state="UNAVAILABLE" 时渲染可见的文字提示', () => {
    render(<MatchRow match={baseMatch({ sync_state: "UNAVAILABLE" })} />);
    // product-status.ts syncStateLabel() 的既有正确翻译,不应该被重新造轮子
    expect(screen.getByText("部分数据暂不可用")).not.toBeNull();
  });

  it('sync_state="STALE" 时原有提示行为不回归', () => {
    render(
      <MatchRow
        match={baseMatch({
          sync_state: "STALE",
          data_updated_at: "2026-08-19T10:00:00Z",
          next_planned_sync_at: "2026-08-20T18:00:00Z",
        })}
      />,
    );
    expect(screen.getByText("数据等待刷新")).not.toBeNull();
    expect(screen.getByText(/等待计划采集/)).not.toBeNull();
  });

  it('sync_state="FRESH" 时不渲染 sync 提示行(保持行干净)', () => {
    render(<MatchRow match={baseMatch({ sync_state: "FRESH" })} />);
    expect(screen.queryByText("数据已更新")).toBeNull();
  });
});

describe("MatchRow 赔率覆盖文案(bug2:过期数据被写死成完整走势)", () => {
  it('full_timeline + freshness=STALE 时不得出现裸的"完整走势",必须带过期限定语', () => {
    render(
      <MatchRow
        match={baseMatch({
          odds_coverage_tier: "full_timeline",
          odds_freshness_state: "STALE",
          odds_last_observed_at: "2026-08-10T08:00:00Z",
        })}
      />,
    );
    expect(screen.queryByText("赔率:完整走势")).toBeNull();
    expect(screen.queryByText(/^赔率:完整走势$/)).toBeNull();
    // 必须能看到一个明确带过期语义的提示(具体措辞不锁死,但必须存在)
    expect(screen.getByText(/过期|已过期|停止更新|最后观测|最后更新/)).not.toBeNull();
  });

  it('full_timeline + freshness=FRESH 时"完整走势"文案照常显示(不回归)', () => {
    render(
      <MatchRow
        match={baseMatch({
          odds_coverage_tier: "full_timeline",
          odds_freshness_state: "FRESH",
        })}
      />,
    );
    expect(screen.getByText("赔率:完整走势")).not.toBeNull();
  });

  it("open_close_only 维持现状逻辑,不受 freshness 字段影响", () => {
    render(
      <MatchRow
        match={baseMatch({
          odds_coverage_tier: "open_close_only",
          odds_freshness_state: "STALE",
        })}
      />,
    );
    expect(screen.getByText("赔率:初盘与临场")).not.toBeNull();
  });
});
