/**
 * 首页第三批改版(2026-09-26):首屏定位语 + 三入口、今晚/明天/本周可点、
 * 停赛期提示、今日精选未发布复盘。
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HomeHero, HERO_TAGLINE } from "@/components/home/HomeHero";
import { HomeMatchExperienceLive } from "@/components/home/HomeMatchExperienceLive";
import { LEAGUE_ZH, LEAGUE_COUNT, formatBeijingMD } from "@/components/matches/zh";
import {
  pickLatestSettledSlip,
  recapDateText,
  recapMatchName,
} from "@/lib/home-recap";
import type { MatchSummary } from "@/lib/api-v1";
import type { HomeMatchCard } from "@/lib/homepage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("HomeHero:一句话定位 + 三个入口", () => {
  it("定位语里的联赛数从联赛配置计算,不写死", () => {
    expect(LEAGUE_COUNT).toBe(Object.keys(LEAGUE_ZH).length);
    expect(HERO_TAGLINE).toBe(`英超、西甲等 ${Object.keys(LEAGUE_ZH).length} 个联赛的比赛与数据`);
    render(<HomeHero />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(HERO_TAGLINE);
  });

  it("三个入口按钮:看比赛 /matches、联赛数据 /leagues、今日精选 /reco", () => {
    render(<HomeHero />);
    const nav = screen.getByRole("navigation", { name: "首页入口" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((a) => a.textContent)).toEqual(["看比赛", "联赛数据", "今日精选"]);
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["/matches", "/leagues", "/reco"]);
  });

  it("文案不含被禁用的词(模型/自研/Bet365)", () => {
    const { container } = render(<HomeHero />);
    expect(container.textContent).not.toMatch(/模型|自研|bet365/i);
  });
});

describe("今日精选复盘(pickLatestSettledSlip)", () => {
  type Slips = Parameters<typeof pickLatestSettledSlip>[0];
  const slip = (over: Record<string, unknown>) =>
    ({
      id: "x",
      slip_date: "2026-09-14",
      title: "意甲精选",
      status: "settled",
      result: "push",
      settled_at: "2026-09-14T19:00:00Z",
      legs: [{ match_desc: "科莫 vs 帕尔马 09/15 00:30" }],
      ...over,
    }) as unknown as Slips[number];

  it("取日期最新的已结算单,输出 日期/比赛/结果", () => {
    const r = pickLatestSettledSlip([
      slip({ id: "old", slip_date: "2026-09-10", result: "win" }),
      slip({ id: "new", slip_date: "2026-09-14", result: "push" }),
    ]);
    expect(r).toEqual({
      dateText: "9月14日",
      matchText: "科莫 vs 帕尔马",
      resultText: "走水",
      tone: "neutral",
    });
  });

  it("作废单/未结算单不算已结算", () => {
    expect(
      pickLatestSettledSlip([
        slip({ status: "voided", result: null }),
        slip({ status: "published", result: null }),
      ]),
    ).toBeNull();
  });

  it("命中 → tone=win;半赢也算 win 色", () => {
    expect(pickLatestSettledSlip([slip({ result: "win" })])?.resultText).toBe("命中");
    expect(pickLatestSettledSlip([slip({ result: "half_win" })])?.tone).toBe("win");
    expect(pickLatestSettledSlip([slip({ result: "lose" })])?.resultText).toBe("未中");
  });

  it("多腿串关:第一场 + 等 N 场", () => {
    const r = pickLatestSettledSlip([
      slip({
        legs: [{ match_desc: "甲 vs 乙 09/15 00:30" }, { match_desc: "丙 vs 丁 09/15 01:30" }],
      }),
    ]);
    expect(r?.matchText).toBe("甲 vs 乙 等 2 场");
  });

  it("空数组返回 null;日期/队名格式化不猜", () => {
    expect(pickLatestSettledSlip([])).toBeNull();
    expect(recapDateText("2026-09-04")).toBe("9月4日");
    expect(recapDateText("坏格式")).toBe("坏格式");
    expect(recapMatchName("A vs B 12/31 23:59")).toBe("A vs B");
    expect(recapMatchName("A vs B")).toBe("A vs B");
  });
});

function card(): HomeMatchCard {
  return {
    match: {
      match_id: 1,
      league_id: 9080,
      season: "2026",
      date_utc: "2026-09-27",
      kickoff_at_utc: "2026-09-27T09:00:00Z",
      round: "22",
      status: "NotStarted",
      home: { team_id: 1, name: "主队", name_en: null, crest_url: null },
      away: { team_id: 2, name: "客队", name_en: null, crest_url: null },
      home_score: null,
      away_score: null,
      win_probability: null,
    } as MatchSummary,
    tip: null,
  };
}

function renderLive(opts: {
  counts?: { today: number; tomorrow: number; week: number } | null;
  brk?: { onBreak: boolean; resumeAt: string | null };
}) {
  vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("no network in test"))));
  render(
    <HomeMatchExperienceLive
      initialFeatured={card()}
      initialSecondary={[]}
      initialCounts={opts.counts === undefined ? { today: 0, tomorrow: 1, week: 2 } : opts.counts}
      initialBreak={opts.brk ?? { onBreak: false, resumeAt: null }}
      initialErrored={false}
    />,
  );
}

describe("今晚/明天/本周:始终可点击,跳到 /matches 对应时间筛选", () => {
  it("三个入口都是链接(包括数量为 0 的「今晚」),href 分别是 today/tomorrow/7d", () => {
    renderLive({});
    const bar = screen.getByTestId("match-counts-bar");
    const links = within(bar).getAllByRole("link");
    expect(links.map((a) => a.textContent?.replace(/\d+$/, ""))).toEqual(["今晚", "明天", "本周"]);
    expect(links.map((a) => a.getAttribute("href"))).toEqual([
      "/matches?window=today",
      "/matches?window=tomorrow",
      "/matches?window=7d",
    ]);
  });
});

describe("停赛期提示", () => {
  it("停赛期且有恢复日 → 显示「国际比赛日,五大联赛 M月D日 恢复」,日期按北京时间", () => {
    // 2026-10-09T18:30Z = 北京 10月10日 02:30
    renderLive({ brk: { onBreak: true, resumeAt: "2026-10-09T18:30:00.000Z" } });
    const note = screen.getByTestId("league-break-note");
    expect(note.textContent).toBe("国际比赛日，五大联赛 10月10日 恢复");
    expect(formatBeijingMD("2026-10-09T18:30:00.000Z")).toBe("10月10日");
  });

  it("停赛期但算不出恢复日 → 不显示这一行", () => {
    renderLive({ brk: { onBreak: true, resumeAt: null } });
    expect(screen.queryByTestId("league-break-note")).toBeNull();
  });

  it("不在停赛期 → 不显示", () => {
    renderLive({ brk: { onBreak: false, resumeAt: "2026-10-09T18:30:00.000Z" } });
    expect(screen.queryByTestId("league-break-note")).toBeNull();
  });
});
