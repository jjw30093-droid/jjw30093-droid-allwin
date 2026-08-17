/**
 * MatchupSection:攻防双方都有数据时,最多只能高亮两个"关键对位"(不产生
 * 0-100 综合评分,只用真实场均数字排序);两队互相都缺该项数据时不得
 * 编造对位。
 *
 * 验收返工二(独立复核第二轮):比较值/基准值改用后端显式给出的
 * comparison_metric / own_comparison_value / conceded_comparison_value /
 * own_baseline_value / conceded_baseline_value 字段,不再用
 * `own_xg_pg ?? own_shots_pg` 猜量纲。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MatchupSection } from "@/components/matches/MatchupSection";
import type { components } from "@/lib/api-types";

afterEach(cleanup);

type MatchupProfile = components["schemas"]["MatchPreviewMatchupProfileDTO"];
type MatchupSituation = components["schemas"]["MatchPreviewMatchupSituationDTO"];

function situation(key: string, label: string, overrides: Partial<MatchupSituation> = {}): MatchupSituation {
  return {
    key,
    label,
    own_shots_pg: 2.0,
    own_xg_pg: 0.3,
    own_xg_complete: true,
    conceded_shots_pg: 1.0,
    conceded_xg_pg: 0.2,
    conceded_xg_complete: true,
    comparison_metric: "xg",
    own_comparison_value: 0.3,
    conceded_comparison_value: 0.2,
    own_baseline_value: 0.15,
    conceded_baseline_value: 0.1,
    comparison_complete: true,
    ...overrides,
  };
}

function profile(overrides: Partial<MatchupProfile> = {}): MatchupProfile {
  return {
    tier: "venue_full",
    matches: 10,
    label_zh: "近 10 个主场",
    situations: [
      situation("regular_play", "运动战"),
      situation("fast_break", "反击", { own_comparison_value: 0.1, conceded_comparison_value: 0.1 }),
      situation("set_piece", "定位球(含角球)", { own_comparison_value: 0.05, conceded_comparison_value: 0.05 }),
      situation("box_shots", "禁区内射门", {
        comparison_metric: "shots",
        own_xg_pg: null, own_xg_complete: false,
        conceded_xg_pg: null, conceded_xg_complete: false,
        own_comparison_value: 6.0, conceded_comparison_value: 4.0,
        own_baseline_value: 8.0, conceded_baseline_value: 6.0, // 贴着/低于基准,默认不构成关键对位
      }),
    ],
    ...overrides,
  };
}

describe("MatchupSection", () => {
  it("最多高亮两个关键对位", () => {
    const home = profile();
    const away = profile({ label_zh: "近 10 个客场" });
    const { container } = render(
      <MatchupSection homeName="主队" awayName="客队" home={home} away={away} />,
    );
    const highlighted = container.querySelectorAll('[class*="rowHighlighted"]');
    expect(highlighted.length).toBeLessThanOrEqual(2);
    expect(highlighted.length).toBeGreaterThan(0);
  });

  it("展示两个方向:主队进攻vs客队防守、客队进攻vs主队防守", () => {
    const home = profile();
    const away = profile({ label_zh: "近 10 个客场" });
    render(<MatchupSection homeName="主队" awayName="客队" home={home} away={away} />);
    expect(screen.getByText("主队 进攻")).not.toBeNull();
    expect(screen.getByText("客队 进攻")).not.toBeNull();
  });

  it("禁区内射门这一行没有 xG 拆分时显示数据不足而不是 0", () => {
    const home = profile();
    const away = profile({ label_zh: "近 10 个客场" });
    render(<MatchupSection homeName="主队" awayName="客队" home={home} away={away} />);
    expect(screen.getAllByText(/数据不足/).length).toBeGreaterThan(0);
  });

  it("禁区内射门(用次数)贴着自己的基准时不该被选中,即使次数量级比其它行的xG大", () => {
    // 回归测试:box_shots 用次数(量级 6~10)天然比其它行的 xG(量级
    // 0.1~0.7)数字更大,但排序现在只看"相对自己基准的超出比例",不看
    // 原始数字大小——box_shots 贴着基准(不超出)时不该赢。
    const home = profile({
      situations: [
        situation("regular_play", "运动战", {
          own_comparison_value: 0.3, own_baseline_value: 0.15, // 超出基准 100%
          conceded_comparison_value: 0.2, conceded_baseline_value: 0.1, // 超出基准 100%
        }),
        situation("box_shots", "禁区内射门", {
          comparison_metric: "shots",
          own_xg_pg: null, own_xg_complete: false,
          own_shots_pg: 8.0, own_comparison_value: 8.0, own_baseline_value: 8.0, // 恰好贴基准
          conceded_xg_pg: null, conceded_xg_complete: false,
          conceded_shots_pg: 6.0, conceded_comparison_value: 6.0, conceded_baseline_value: 6.0,
        }),
      ],
    });
    const away = profile({ label_zh: "近 10 个客场", situations: home.situations });
    const { container } = render(
      <MatchupSection homeName="主队" awayName="客队" home={home} away={away} />,
    );
    const highlighted = [...container.querySelectorAll('[class*="rowHighlighted"]')];
    const highlightedLabels = highlighted.map((el) => el.querySelector('[class*="rowLabel"]')?.textContent);
    expect(highlightedLabels).not.toContain("禁区内射门");
    expect(highlightedLabels).toContain("运动战");
  });

  it("反例(验收返工二):运动战双方均处联赛平均,定位球双方均显著高于各自基准——必须选定位球,不能因运动战原始xG基数大而选运动战", () => {
    const home = profile({
      situations: [
        // 运动战:原始 xG 基数天然更大(1.2/1.0),但双方都恰好等于各自
        // 联赛基准——不构成"关键对位"。
        situation("regular_play", "运动战", {
          own_comparison_value: 1.2, own_baseline_value: 1.2,
          conceded_comparison_value: 1.0, conceded_baseline_value: 1.0,
        }),
        // 反击:同样贴着基准,不构成关键对位。
        situation("fast_break", "反击", {
          own_comparison_value: 0.2, own_baseline_value: 0.2,
          conceded_comparison_value: 0.15, conceded_baseline_value: 0.15,
        }),
        // 定位球:原始 xG 基数小(0.35/0.30),但双方都显著高于各自基准
        // (0.35 > 0.15,0.30 > 0.12)——这才是真正的关键对位。
        situation("set_piece", "定位球(含角球)", {
          own_comparison_value: 0.35, own_baseline_value: 0.15,
          conceded_comparison_value: 0.30, conceded_baseline_value: 0.12,
        }),
        situation("box_shots", "禁区内射门", {
          comparison_metric: "shots",
          own_xg_pg: null, own_xg_complete: false,
          conceded_xg_pg: null, conceded_xg_complete: false,
          own_comparison_value: null, own_baseline_value: null,
          conceded_comparison_value: null, conceded_baseline_value: null,
          comparison_complete: false,
        }),
      ],
    });
    const away = profile({ label_zh: "近 10 个客场", situations: home.situations });

    const { container } = render(
      <MatchupSection homeName="主队" awayName="客队" home={home} away={away} />,
    );
    const highlighted = [...container.querySelectorAll('[class*="rowHighlighted"]')];
    const highlightedLabels = highlighted.map((el) => el.querySelector('[class*="rowLabel"]')?.textContent);
    expect(highlightedLabels).toContain("定位球(含角球)");
    expect(highlightedLabels).not.toContain("运动战");
  });

  it("双方都没有超过基准时,不产生关键对位(不得退回裸 xG 比较)", () => {
    const home = profile({
      situations: [
        situation("regular_play", "运动战", {
          own_comparison_value: 1.0, own_baseline_value: 1.2, // 低于基准
          conceded_comparison_value: 0.9, conceded_baseline_value: 1.0, // 低于基准
        }),
      ],
    });
    const away = profile({ label_zh: "近 10 个客场", situations: home.situations });
    render(<MatchupSection homeName="主队" awayName="客队" home={home} away={away} />);
    expect(screen.getByText(/暂无法给出关键对位/)).not.toBeNull();
  });

  it("验收返工三:一方 mixed、另一方 venue_full 时该方向不得产生关键对位,且必须显示样本口径不同的原因", () => {
    const home = profile({
      tier: "mixed",
      situations: [
        situation("regular_play", "运动战", {
          own_comparison_value: 1.0, own_baseline_value: 0.3, // 主队进攻本身远超基准
        }),
      ],
    });
    const away = profile({
      tier: "venue_full",
      label_zh: "近 10 个客场",
      situations: [
        situation("regular_play", "运动战", {
          conceded_comparison_value: 0.8, conceded_baseline_value: 0.2, // 客队防守让出也远超基准
        }),
      ],
    });
    const { container } = render(
      <MatchupSection homeName="主队" awayName="客队" home={home} away={away} />,
    );
    // 主队(mixed)进攻 vs 客队(venue_full)防守 这个方向口径不兼容,
    // 即使双方数字看起来都"超基准",也不能标记关键对位,且必须明确说
    // "样本口径不同",不能用笼统的"可比数据不足"掩盖真正原因。
    const highlighted = container.querySelectorAll('[class*="rowHighlighted"]');
    expect(highlighted.length).toBe(0);
    expect(screen.getByText(/样本口径不同,暂不作高低判断/)).not.toBeNull();
  });

  it("验收返工三:venue_full 与 venue_partial 不再视为同一口径,不得产生关键对位", () => {
    const home = profile({ tier: "venue_full" });
    const away = profile({ tier: "venue_partial", label_zh: "近 7 个客场" });
    const { container } = render(
      <MatchupSection homeName="主队" awayName="客队" home={home} away={away} />,
    );
    const highlighted = container.querySelectorAll('[class*="rowHighlighted"]');
    expect(highlighted.length).toBe(0);
    expect(screen.getByText(/样本口径不同,暂不作高低判断/)).not.toBeNull();
  });

  it("验收返工三:双方都是 mixed 时(此前旧实现视为可比)也不得产生关键对位", () => {
    const home = profile({ tier: "mixed" });
    const away = profile({ tier: "mixed", label_zh: "近 10 场(已合并主客场)" });
    const { container } = render(
      <MatchupSection homeName="主队" awayName="客队" home={home} away={away} />,
    );
    const highlighted = container.querySelectorAll('[class*="rowHighlighted"]');
    expect(highlighted.length).toBe(0);
    expect(screen.getByText(/样本口径不同,暂不作高低判断/)).not.toBeNull();
  });

  it("tier 不兼容时原始数字(射门次数/xG)仍然照常展示,不因不可比而隐藏", () => {
    const home = profile({ tier: "mixed" });
    const away = profile({ tier: "venue_full", label_zh: "近 10 个客场" });
    render(<MatchupSection homeName="主队" awayName="客队" home={home} away={away} />);
    // profile() 默认 regular_play own_shots_pg=2.0,数字应该仍然可见。
    expect(screen.getAllByText(/2\.0 次\/场/).length).toBeGreaterThan(0);
  });

  it("页面不出现 markdown 星号源码(如 **同时**),只显示正常中文", () => {
    const home = profile();
    const away = profile({ label_zh: "近 10 个客场" });
    render(<MatchupSection homeName="主队" awayName="客队" home={home} away={away} />);
    expect(document.body.textContent).not.toMatch(/\*\*/);
  });

  it("验收返工二(真实数据复现,比赛5868022/球队10205):own_xg_pg缺失时不得退回own_shots_pg跟xG基准比较", () => {
    // 真实只读数据(mode=ro,team_matchup_profile(conn, 10205, 87,
    // '2026-08-23T15:00:00Z', is_home=False)):运动战 own_shots_pg=6.1、
    // own_xg_pg=null、own_xg_complete=false、own_baseline_value=0.696——
    // 后端现在对 own_xg_complete=false 的情况直接给 own_comparison_value=null,
    // 不再有"6.1(次数)当成xG用"这条路径,这条测试确认前端如实呈现该
    // null,不自己重新拼一个 fallback。
    const home = profile({
      situations: [
        situation("regular_play", "运动战", {
          own_shots_pg: 6.1, own_xg_pg: null, own_xg_complete: false,
          own_comparison_value: null, own_baseline_value: 0.696,
          comparison_complete: false,
        }),
      ],
    });
    const away = profile({
      label_zh: "近 10 个客场",
      situations: [
        situation("regular_play", "运动战", {
          conceded_xg_pg: 1.5, conceded_xg_complete: true,
          conceded_comparison_value: 1.5, conceded_baseline_value: 1.008,
          // 客队自己的"进攻"方向(own_*)在这条用例里不是关注点,显式清空,
          // 避免复用 situation() 默认的 own_comparison_value/own_baseline_value
          // 意外在"客队进攻 vs 主队防守"方向上凑出一个无关的合格结果,
          // 干扰这条测试真正要验证的"主队运动战因 own 量纲缺失不得合格"。
          own_comparison_value: null, own_baseline_value: null, comparison_complete: false,
        }),
      ],
    });
    const { container } = render(
      <MatchupSection homeName="主队" awayName="客队" home={home} away={away} />,
    );
    const highlighted = [...container.querySelectorAll('[class*="rowHighlighted"]')];
    const highlightedLabels = highlighted.map((el) => el.querySelector('[class*="rowLabel"]')?.textContent);
    // own_comparison_value=null——运动战这一类不得因为退回次数而"合格"。
    expect(highlightedLabels).not.toContain("运动战");
    expect(screen.getByText(/暂无法给出关键对位/)).not.toBeNull();
  });

  it("验收返工二:box_shots 类型必须用射门次数比较,不得使用xG", () => {
    const home = profile({
      situations: [
        situation("box_shots", "禁区内射门", {
          comparison_metric: "shots",
          own_shots_pg: 10.0, own_xg_pg: 5.0, own_xg_complete: true, // xG 字段即使存在也不该被用
          own_comparison_value: 10.0, own_baseline_value: 8.0,
        }),
      ],
    });
    const away = profile({
      label_zh: "近 10 个客场",
      situations: [
        situation("box_shots", "禁区内射门", {
          comparison_metric: "shots",
          conceded_shots_pg: 9.0, conceded_xg_pg: 4.0, conceded_xg_complete: true,
          conceded_comparison_value: 9.0, conceded_baseline_value: 7.0,
        }),
      ],
    });
    const { container } = render(
      <MatchupSection homeName="主队" awayName="客队" home={home} away={away} />,
    );
    // 用次数(10.0>8.0, 9.0>7.0)判定应当合格——这条本身应该保持 GREEN,
    // 用来确认修复没有连带破坏 box_shots 合法使用次数比较的路径。
    const highlighted = [...container.querySelectorAll('[class*="rowHighlighted"]')];
    const highlightedLabels = highlighted.map((el) => el.querySelector('[class*="rowLabel"]')?.textContent);
    expect(highlightedLabels).toContain("禁区内射门");
  });

  it("两队所有环节都无可比数据时,不编造关键对位", () => {
    const empty: MatchupProfile = {
      tier: "unavailable",
      matches: 0,
      label_zh: "暂无可比较的历史比赛",
      situations: [
        situation("regular_play", "运动战", {
          own_shots_pg: null, own_xg_pg: null, own_xg_complete: false,
          conceded_shots_pg: null, conceded_xg_pg: null, conceded_xg_complete: false,
          own_comparison_value: null, conceded_comparison_value: null,
          own_baseline_value: null, conceded_baseline_value: null,
          comparison_complete: false,
        }),
      ],
    };
    render(<MatchupSection homeName="主队" awayName="客队" home={empty} away={empty} />);
    expect(screen.getByText(/样本口径不同,暂不作高低判断/)).not.toBeNull();
  });
});
