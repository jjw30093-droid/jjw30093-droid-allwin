import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SafeSceneCard } from "@/components/studio/SafeSceneCards";
import { exportFilename } from "@/components/studio/api";
import type { DouyinSafeProfile } from "@/components/studio/types";

const metric = (
  key: string,
  label: string,
  home: number,
  away: number,
  direction: "higher" | "lower" = "higher",
) => ({
  key,
  label,
  unit: "/场",
  direction,
  home: { value: home, display: `${home}/场`, rank: 2, rank_total: 16 },
  away: { value: away, display: `${away}/场`, rank: 9, rank_total: 16 },
});

const profile: DouyinSafeProfile = {
  profile_id: "douyin-safe-v1",
  profile_version: 1,
  source_hash: "abcdef1234567890",
  data_cutoff_at: "2026-07-28T07:36:12Z",
  match: {
    match_id: 5104968,
    league_name: "挪威超",
    season: "2026",
    round: "16",
    kickoff_at_utc: "2026-07-31T17:00:00Z",
    home: { team_id: 8007, name: "瓦勒伦加", crest_url: null },
    away: { team_id: 8448, name: "汉坎", crest_url: null },
  },
  scenes: [
    { id: "cover", index: "01", label: "比赛封面", title: "两队打法差在哪？", metrics: [], conclusions: [] },
    {
      id: "possession",
      index: "02",
      label: "控球与组织",
      title: "谁更主动控制比赛？",
      metrics: [
        metric("possession", "控球率", 49.3, 46.1),
        metric("passes", "准确传球", 357.9, 352),
        metric("regains", "前场夺回", 2.9, 2.6),
      ],
      conclusions: ["瓦勒伦加在控球率这一项更高，相关比赛环节更活跃。"],
    },
    { id: "threat", index: "03", label: "禁区威胁", title: "谁更容易进入危险区域？", metrics: [], conclusions: [] },
    { id: "width", index: "04", label: "边路与定位球", title: "谁更依赖边路与定位球？", metrics: [], conclusions: [] },
    {
      id: "defense",
      index: "05",
      label: "无球与防守压力",
      title: "谁承担更多无球压力？",
      metrics: [metric("xga", "xGA", 1.89, 1.77, "lower")],
      conclusions: ["汉坎的xGA更低，赛季防守数据相对更稳。"],
    },
    {
      id: "summary",
      index: "06",
      label: "对位总结",
      title: "一页看懂两队风格",
      metrics: [],
      conclusions: ["两队组织方式不同。"],
      recent_form: { home: ["W", "D", "W"], away: ["L", "D", "W"] },
      risk_notes: ["赛前阵容尚未稳定覆盖。"],
      cta: "关注公众号，查看每日比赛数据拆解",
    },
  ],
  script_sections: [],
  subtitle_cues: [],
  titles: [],
  xiaohongshu_text: "",
  wechat_summary: "",
  source_note: "",
};

describe("douyin-safe Studio cards", () => {
  it("renders direct values, ranks and lower-is-better note without a radar chart", () => {
    const { container, rerender } = render(
      <SafeSceneCard profile={profile} scene="possession" height={1920} />,
    );
    expect(screen.getByText("49.3/场")).toBeTruthy();
    expect(screen.getAllByText("联赛第 2").length).toBeGreaterThan(0);
    expect(container.textContent).not.toContain("雷达");

    rerender(<SafeSceneCard profile={profile} scene="defense" height={1350} />);
    expect(screen.getByText("越低越好")).toBeTruthy();
  });

  it("renders six scene identities without betting-sensitive copy", () => {
    const forbidden = [
      "胜平负", "主胜", "客胜", "平局", "赔率", "盘口", "投注",
      "推荐", "稳胆", "命中率", "收益", "红单",
    ];
    for (const scene of profile.scenes) {
      const { container, unmount } = render(
        <SafeSceneCard profile={profile} scene={scene.id} height={1920} />,
      );
      expect(container.querySelector(`[data-testid="safe-scene-${scene.id}"]`)).toBeTruthy();
      for (const term of forbidden) expect(container.textContent).not.toContain(term);
      unmount();
    }
  });

  it("maps snake-case safe-profile crest URLs into both team marks", () => {
    const withCrests = structuredClone(profile);
    withCrests.match.home.crest_url = "/api/v1/media/team-crests/fotmob/8007.png?v=home";
    withCrests.match.away.crest_url = "/api/v1/media/team-crests/fotmob/8448.png?v=away";
    render(<SafeSceneCard profile={withCrests} scene="cover" height={1920} />);
    expect(screen.getByAltText("瓦勒伦加队徽").getAttribute("src")).toContain("8007.png");
    expect(screen.getByAltText("汉坎队徽").getAttribute("src")).toContain("8448.png");
  });

  it("builds deterministic safe filenames with profile, cutoff and source hash", () => {
    expect(
      exportFilename(
        5104968,
        "cover_1080x1920",
        "png",
        null,
        "2026-07-28T07:36:12Z",
        "douyin-safe-v1",
        "abcdef1234567890",
      ),
    ).toBe(
      "allwin_douyin-safe-v1_5104968_cover_1080x1920_cutoff-2026-07-28_no-model_abcdef123456.png",
    );
  });
});
