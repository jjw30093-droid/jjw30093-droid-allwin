import { describe, expect, it } from "vitest";
import {
  buildMatchesHref,
  type MatchFilters,
} from "@/lib/match-filters";
import { syncStateLabel } from "@/lib/product-status";

const filters: MatchFilters = {
  date: "2026-08-01",
  league: 59,
  status: "upcoming",
  window: "7d",
  content: "odds",
  q: "瓦勒伦加",
  page: 3,
};

describe("match discovery URLs", () => {
  it("组合筛选和分页时保留全部 URL query", () => {
    const url = new URL(
      buildMatchesHref(filters, { page: 4 }),
      "https://allwin.example",
    );
    expect(Object.fromEntries(url.searchParams)).toEqual({
      date: "2026-08-01",
      league: "59",
      content: "odds",
      q: "瓦勒伦加",
      page: "4",
    });
  });

  it("默认未来七天未开赛视图有稳定简洁 URL", () => {
    expect(
      buildMatchesHref({ status: "upcoming", window: "7d", page: 1 }, {}),
    ).toBe("/matches");
  });
});

describe("public freshness labels", () => {
  it("不再把 freshness 命名成直播状态", () => {
    expect(syncStateLabel("FRESH")).toBe("数据已更新");
    expect(syncStateLabel("STALE")).toBe("数据等待刷新");
    expect(syncStateLabel("UNAVAILABLE")).toBe("部分数据暂不可用");
  });
});
