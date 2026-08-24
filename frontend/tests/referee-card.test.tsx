/**
 * RefereeCard(2026-08-25,裁判信息卡)。降级链:完整 stats → 完整卡;
 * 只有姓名 → 只渲染姓名行;无姓名 → 整卡 null。评级文字直用服务端
 * average_type,不自算阈值。
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { RefereeCard } from "@/components/matches/RefereeCard";
import type { MatchDetailResponse } from "@/lib/api-v1";

afterEach(cleanup);

type Match = MatchDetailResponse["match"];
type RefereeStat = NonNullable<Match["referee_stats"]>[number];

function match(overrides: Partial<Match> = {}): Match {
  return {
    match_id: 1,
    league_id: 87,
    season: "2026/2027",
    date_utc: "2026-08-24",
    status: "NotStarted",
    home: { team_id: 1, name: "主队", name_en: "Home", crest_url: null },
    away: { team_id: 2, name: "客队", name_en: "Away", crest_url: null },
    referee: null,
    referee_id: null,
    referee_country: null,
    referee_country_code: null,
    referee_stats: [],
    ...overrides,
  };
}

const FULL_STATS: RefereeStat[] = [
  { type: "matches", value: 38, value_type: "total",
    average: null, total: null, average_type: null,
    fill_percentage: null, average_percentage: null },
  { type: "yellowCards", value: 4.11, value_type: "perMatch",
    average: 4.49, total: 156, average_type: "below",
    fill_percentage: 37.07, average_percentage: 50 },
  { type: "fouls", value: 24.74, value_type: "perMatch",
    average: 25.05, total: 767, average_type: "average",
    fill_percentage: 45.49, average_percentage: 50 },
];

describe("RefereeCard", () => {
  it("无裁判姓名时整卡不渲染", () => {
    const { container } = render(<RefereeCard match={match()} />);
    expect(container.querySelector('[data-testid="referee-card"]')).toBeNull();
  });

  it("只有姓名(存量数据 ~71% 场次)时只渲染姓名行,不画空进度条", () => {
    const { container } = render(
      <RefereeCard match={match({ referee: "Mischa Kellerhals" })} />,
    );
    expect(screen.getByText("Mischa Kellerhals")).not.toBeNull();
    expect(screen.queryByText("黄牌")).toBeNull();
    expect(screen.queryByText("犯规")).toBeNull();
    // 无 referee_id 也没有头像 img
    expect(container.querySelector("img")).toBeNull();
  });

  it("完整 stats:只上 perMatch 两项,值 1 位小数,评级用服务端 average_type 的中文", () => {
    render(
      <RefereeCard
        match={match({
          referee: "Victor García Verdura",
          referee_id: 1001072330,
          referee_country: "Spain",
          referee_stats: FULL_STATS,
        })}
      />,
    );
    expect(screen.getByText("黄牌")).not.toBeNull();
    expect(screen.getByText("4.1")).not.toBeNull();
    expect(screen.getByText("低于平均水平")).not.toBeNull();
    expect(screen.getByText("犯规")).not.toBeNull();
    expect(screen.getByText("24.7")).not.toBeNull();
    expect(screen.getByText("平均水平")).not.toBeNull();
    expect(screen.getByText("Spain")).not.toBeNull();
    // total 项(matches=38)不上卡
    expect(screen.queryByText("38")).toBeNull();
  });

  it("进度条宽度与均值刻度位置直用服务端百分比,不重算", () => {
    const { container } = render(
      <RefereeCard
        match={match({ referee: "R", referee_stats: [FULL_STATS[1]] })}
      />,
    );
    const fill = container.querySelector('[class*="barFill"]') as HTMLElement;
    expect(fill.style.width).toBe("37.07%");
    const tick = container.querySelector('[class*="avgTick"]') as HTMLElement;
    expect(tick.style.left).toBe("50%");
  });

  it("头像来自 FotMob CDN(referee_id 拼 URL),加载失败回退文字不留裂图", () => {
    const { container } = render(
      <RefereeCard
        match={match({ referee: "Victor García Verdura", referee_id: 1001072330 })}
      />,
    );
    const img = container.querySelector("img")!;
    expect(img.getAttribute("src")).toContain(
      "images.fotmob.com/image_resources/playerimages/1001072330.png",
    );
    fireEvent.error(img);
    expect(container.querySelector("img")).toBeNull();
    expect(
      container.querySelector('[data-testid="player-avatar-fallback"]'),
    ).not.toBeNull();
  });
});
