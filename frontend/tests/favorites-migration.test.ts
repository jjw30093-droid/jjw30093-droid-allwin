/**
 * lib/favorites:登录后 localStorage 关注一次性迁移(2026-08-21)。
 *
 * 硬约束(静默数据丢失 = 比原 bug 更严重的事故):
 * - 只有确认 2xx 的 id 才从 localStorage 移除,失败的留在原地(本地列表
 *   本身就是重试队列);
 * - 倒序串行(最旧先 POST),让服务端 created_at 顺序 ≈ 本地"最近在前";
 * - GET 401 = 匿名:零 POST、零删除;
 * - 迁移途中 POST 收到 401:整体中止,后续条目不再尝试、未确认的不删。
 */

import { afterEach, describe, expect, it, vi } from "vitest";

const KEY = "allwin-followed-matches";
const JSON_HEADERS = new Headers({ "content-type": "application/json" });

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
  window.localStorage.clear();
});

type Call = { url: string; method: string; body: unknown };

function stubFetch(opts: {
  getStatus?: number;
  serverIds?: number[];
  postStatus?: (matchId: number) => number;
}): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: unknown, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ url, method, body });
      if (method === "GET") {
        const status = opts.getStatus ?? 200;
        const payload =
          status === 200
            ? {
                favorites: (opts.serverIds ?? []).map((id) => ({
                  match_id: id,
                  created_at: "2026-08-01T00:00:00Z",
                })),
              }
            : { code: "HTTP_401", message: "需要登录", details: null };
        return Promise.resolve(
          new Response(JSON.stringify(payload), { status, headers: JSON_HEADERS }),
        );
      }
      const status = opts.postStatus?.(body?.match_id as number) ?? 200;
      const payload =
        status === 200
          ? { status: "ok" }
          : { code: `HTTP_${status}`, message: "err", details: null };
      return Promise.resolve(
        new Response(JSON.stringify(payload), { status, headers: JSON_HEADERS }),
      );
    }),
  );
  return calls;
}

async function load() {
  const mod = await import("@/lib/favorites");
  return mod;
}

describe("loadFavorites:登录态迁移", () => {
  it("全部成功:最旧先 POST,localStorage 整个 key 删掉,合并列表最近在前", async () => {
    window.localStorage.setItem(KEY, JSON.stringify([3, 2, 1])); // 最近在前
    const calls = stubFetch({ serverIds: [9] });
    const { loadFavorites } = await load();
    const state = await loadFavorites();

    const posts = calls.filter((c) => c.method === "POST");
    expect(posts.map((c) => (c.body as { match_id: number }).match_id)).toEqual([1, 2, 3]);
    expect(window.localStorage.getItem(KEY)).toBeNull();
    expect(state).toEqual({ authenticated: true, ids: [3, 2, 1, 9] });
  });

  it("单条 500:只有成功的 id 被删,失败的留在本地当重试队列", async () => {
    window.localStorage.setItem(KEY, JSON.stringify([3, 2, 1]));
    stubFetch({ postStatus: (id) => (id === 2 ? 500 : 200) });
    const { loadFavorites } = await load();
    const state = await loadFavorites();

    expect(JSON.parse(window.localStorage.getItem(KEY)!)).toEqual([2]);
    expect(state.authenticated).toBe(true);
    if (state.authenticated) {
      expect(state.ids).toContain(1);
      expect(state.ids).toContain(3);
      expect(state.ids).not.toContain(2); // 未确认写入的不冒充已迁移
    }
  });

  it("GET 401(匿名):零 POST、零删除", async () => {
    window.localStorage.setItem(KEY, JSON.stringify([3, 2, 1]));
    const calls = stubFetch({ getStatus: 401 });
    const { loadFavorites } = await load();
    const state = await loadFavorites();

    expect(state).toEqual({ authenticated: false });
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
    expect(JSON.parse(window.localStorage.getItem(KEY)!)).toEqual([3, 2, 1]);
  });

  it("迁移途中 401:整体中止,后续不再 POST,未确认的一条不删", async () => {
    window.localStorage.setItem(KEY, JSON.stringify([3, 2, 1]));
    const calls = stubFetch({ postStatus: (id) => (id === 1 ? 401 : 200) }); // 第一条就 401
    const { loadFavorites } = await load();
    await loadFavorites();

    const posts = calls.filter((c) => c.method === "POST");
    expect(posts.map((c) => (c.body as { match_id: number }).match_id)).toEqual([1]);
    expect(JSON.parse(window.localStorage.getItem(KEY)!)).toEqual([3, 2, 1]);
  });

  it("模块级缓存:两次 loadFavorites 只打一次 GET;reset 后重新请求", async () => {
    const calls = stubFetch({ serverIds: [7] });
    const { loadFavorites, resetFavoritesCache } = await load();
    await loadFavorites();
    await loadFavorites();
    expect(calls.filter((c) => c.method === "GET")).toHaveLength(1);
    resetFavoritesCache();
    await loadFavorites();
    expect(calls.filter((c) => c.method === "GET")).toHaveLength(2);
  });
});

describe("addFavorite/removeFavorite:mutation 后缓存原位更新", () => {
  it("addFavorite 成功后 loadFavorites 立即包含新 id(不再打 GET)", async () => {
    const calls = stubFetch({ serverIds: [7] });
    const { loadFavorites, addFavorite } = await load();
    await loadFavorites();
    await addFavorite(42);
    const state = await loadFavorites();
    expect(state).toEqual({ authenticated: true, ids: [42, 7] });
    expect(calls.filter((c) => c.method === "GET")).toHaveLength(1);
  });

  it("removeFavorite 成功后 id 从缓存消失", async () => {
    stubFetch({ serverIds: [7, 8] });
    const { loadFavorites, removeFavorite } = await load();
    await loadFavorites();
    await removeFavorite(7);
    const state = await loadFavorites();
    expect(state).toEqual({ authenticated: true, ids: [8] });
  });
});
