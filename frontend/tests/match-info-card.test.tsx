/**
 * MatchInfoCard:比赛信息卡(球场/天气/主裁,2026-08-20)。
 *
 * 覆盖:
 * - 三行各自独立判空,互不影响;
 * - 三个字段全空时整卡不渲染(不留空框);
 * - 天气关键词命中时给中文类别,命不中时如实展示英文原文(不猜译文)。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MatchInfoCard } from "@/components/matches/MatchInfoCard";
import type { MatchDetailResponse } from "@/lib/api-v1";

afterEach(cleanup);

type Match = MatchDetailResponse["match"];

function match(overrides: Partial<Match> = {}): Match {
  return {
    match_id: 1,
    league_id: 47,
    season: "2026/2027",
    date_utc: "2026-08-20",
    status: "NotStarted",
    home: { team_id: 1, name: "主队", name_en: "Home", crest_url: null },
    away: { team_id: 2, name: "客队", name_en: "Away", crest_url: null },
    referee: null,
    temperature_c: null,
    wind_speed_kmh: null,
    weather_description: null,
    venue_name: null,
    venue_city: null,
    venue_country: null,
    ...overrides,
  };
}

describe("MatchInfoCard", () => {
  it("全部字段为空时不渲染卡片", () => {
    const { container } = render(<MatchInfoCard match={match()} />);
    expect(container.querySelector('[data-testid="match-info-card"]')).toBeNull();
  });

  it("只有裁判时只显示裁判一行", () => {
    render(<MatchInfoCard match={match({ referee: "Mischa Kellerhals" })} />);
    expect(screen.getByText("主裁")).not.toBeNull();
    expect(screen.getByText("Mischa Kellerhals")).not.toBeNull();
    expect(screen.queryByText("球场")).toBeNull();
    expect(screen.queryByText("天气")).toBeNull();
  });

  it("场馆名+城市+国家用 · 拼接;只有名字时不拼多余的分隔符", () => {
    const { rerender } = render(
      <MatchInfoCard
        match={match({
          venue_name: "Nye Fredrikstad Stadion",
          venue_city: "Fredrikstad",
          venue_country: "Norway",
        })}
      />,
    );
    expect(
      screen.getByText("Nye Fredrikstad Stadion · Fredrikstad · Norway"),
    ).not.toBeNull();

    rerender(<MatchInfoCard match={match({ venue_name: "Emirates Stadium" })} />);
    expect(screen.getByText("Emirates Stadium")).not.toBeNull();
  });

  it("天气关键词命中(Wind)时显示中文类别,并拼上温度与风速", () => {
    render(
      <MatchInfoCard
        match={match({
          weather_description: "Partly Cloudy/Wind",
          temperature_c: 18,
          wind_speed_kmh: 9,
        })}
      />,
    );
    expect(screen.getByText("大风 · 18°C · 风速 9 km/h")).not.toBeNull();
  });

  it("天气描述关键词不命中时如实展示英文原文,不猜译文", () => {
    render(<MatchInfoCard match={match({ weather_description: "Foobar Weather" })} />);
    expect(screen.getByText("Foobar Weather")).not.toBeNull();
  });

  it("只有温度、没有天气描述时也能单独显示温度", () => {
    render(<MatchInfoCard match={match({ temperature_c: 22 })} />);
    expect(screen.getByText("22°C")).not.toBeNull();
  });
});
