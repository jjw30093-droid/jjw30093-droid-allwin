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
    venue_capacity: null,
    venue_surface: null,
    venue_lat: null,
    venue_long: null,
    weather_localized_key: null,
    weather_icon_code: null,
    referee_id: null,
    referee_country: null,
    referee_country_code: null,
    referee_stats: [],
    ...overrides,
  };
}

describe("MatchInfoCard", () => {
  it("全部字段为空时不渲染卡片", () => {
    const { container } = render(<MatchInfoCard match={match()} />);
    expect(container.querySelector('[data-testid="match-info-card"]')).toBeNull();
  });

  it("主裁行已移出本卡(2026-08-24 拆到 RefereeCard):只有裁判时整卡不渲染", () => {
    const { container } = render(
      <MatchInfoCard match={match({ referee: "Mischa Kellerhals" })} />,
    );
    expect(container.querySelector('[data-testid="match-info-card"]')).toBeNull();
    expect(screen.queryByText("主裁")).toBeNull();
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

  // ── 2026-08-24 对齐 FotMob 场地天气卡(0010 新字段)────────────────────

  it("容纳人数与场地表面各自独立成行,surface 走官方中文对照", () => {
    render(
      <MatchInfoCard
        match={match({
          venue_name: "Estadio El Sadar",
          venue_capacity: 23576,
          venue_surface: "grass",
        })}
      />,
    );
    expect(screen.getByText("容纳人数")).not.toBeNull();
    expect(screen.getByText("23,576")).not.toBeNull();
    expect(screen.getByText("场地表面")).not.toBeNull();
    expect(screen.getByText("天然草皮")).not.toBeNull();
  });

  it("surface 枚举命不中(新值)时如实展示原文,不猜译文", () => {
    render(<MatchInfoCard match={match({ venue_surface: "hybrid grass" })} />);
    expect(screen.getByText("hybrid grass")).not.toBeNull();
  });

  it("weather_localized_key 官方对照优先于 description 关键词", () => {
    render(
      <MatchInfoCard
        match={match({
          weather_localized_key: "weather_condition_partly_cloudy",
          weather_description: "Partly Cloudy/Wind", // 关键词会命中"大风",但 key 优先
          temperature_c: 27,
        })}
      />,
    );
    expect(screen.getByText("局部多云 · 27°C")).not.toBeNull();
  });

  it("localizedKey 命不中官方表时退回 description 关键词匹配", () => {
    render(
      <MatchInfoCard
        match={match({
          weather_localized_key: "weather_condition_never_seen",
          weather_description: "Heavy Rain",
        })}
      />,
    );
    expect(screen.getByText("雨")).not.toBeNull();
  });

  it("经纬度齐全时球场名是 Google Maps 链接;缺任一维度时不渲染链接", () => {
    const { container, rerender } = render(
      <MatchInfoCard
        match={match({
          venue_name: "Estadio El Sadar",
          venue_lat: 42.796676994,
          venue_long: -1.637141258,
        })}
      />,
    );
    const link = container.querySelector("a");
    expect(link).not.toBeNull();
    expect(link!.getAttribute("href")).toBe(
      "https://www.google.com/maps/search/42.796676994,-1.637141258/@42.796676994,-1.637141258&map_action=map",
    );

    rerender(
      <MatchInfoCard
        match={match({ venue_name: "Estadio El Sadar", venue_lat: 42.8, venue_long: null })}
      />,
    );
    expect(container.querySelector("a")).toBeNull();
    expect(screen.getByText("Estadio El Sadar")).not.toBeNull();
  });
});
