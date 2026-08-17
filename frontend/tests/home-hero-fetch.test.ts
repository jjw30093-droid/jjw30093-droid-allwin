/**
 * 首页重点位候选请求必须覆盖完整 7 天窗口的判据(2026-08-16 真实数据完整性 bug)。
 *
 * frontend/lib/homepage.ts::selectHeroPair 的选择逻辑本身是对的——优先选
 * "免费且已有真实概率"的比赛,找不到才退化成"任意免费比赛"。真正的 bug 在
 * 更上游:两处调用方(app/page.tsx::getHomePageData SSR、
 * components/home/HomeMatchExperienceLive.tsx::fetchHomeData 客户端刷新)
 * 都只请求 `/api/v1/matches?status=upcoming&window=7d&limit=8`——如果这
 * 原始 API 顺序的前 8 条恰好都是锁定联赛或还没算出概率,selectHeroPair 就
 * 永远选不到窗口里其实存在的免费+已发布概率的比赛。
 *
 * 修复方式:两处请求都加上服务端 opt-in 的 `boost=free_predicted`
 * (backend/api/routes_public.py::list_matches),让服务端在完整候选窗口内
 * (不受 limit 截断)把这场比赛顶进 limit=8 截断线以内。这里只验证"请求确实
 * 带上了这个参数"——服务端一侧的确定性选场行为由
 * tests/backend/test_matches_season_and_odds_filter.py::
 * TestHomepageHeroBoostFreePredicted 覆盖。
 */

import { afterEach, describe, expect, it, vi } from "vitest";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const emptyMatchList = {
  limit: 8,
  offset: 0,
  season: null,
  available_seasons: [],
  total: 0,
  matches: [],
};

function stubFetchCollectingUrls(calls: string[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: unknown) => {
      calls.push(String(input));
      return Promise.resolve(jsonResponse(emptyMatchList));
    }),
  );
}

describe("fetchHomeData(HomeMatchExperienceLive.tsx,浏览器挂载后刷新)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("重点位候选请求(window=7d&limit=8)必须带 boost=free_predicted", async () => {
    const calls: string[] = [];
    stubFetchCollectingUrls(calls);

    const { fetchHomeData } = await import("@/components/home/HomeMatchExperienceLive");
    await fetchHomeData();

    const heroCall = calls.find((u) => u.includes("window=7d") && u.includes("limit=8"));
    expect(heroCall).toBeDefined();
    expect(heroCall).toContain("boost=free_predicted");
  });

  it("今晚/明天计数请求不需要这个参数(它们不做重点位候选筛选)", async () => {
    const calls: string[] = [];
    stubFetchCollectingUrls(calls);

    const { fetchHomeData } = await import("@/components/home/HomeMatchExperienceLive");
    await fetchHomeData();

    const todayCall = calls.find((u) => u.includes("window=today"));
    expect(todayCall).toBeDefined();
    expect(todayCall).not.toContain("boost=");
  });
});

describe("getHomePageData(app/page.tsx,SSR 匿名口径初始数据)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("SSR 重点位候选请求同样必须带 boost=free_predicted——与客户端刷新同一套判据", async () => {
    const calls: string[] = [];
    stubFetchCollectingUrls(calls);

    const { getHomePageData } = await import("@/app/page");
    await getHomePageData();

    const heroCall = calls.find((u) => u.includes("window=7d") && u.includes("limit=8"));
    expect(heroCall).toBeDefined();
    expect(heroCall).toContain("boost=free_predicted");
  });
});
