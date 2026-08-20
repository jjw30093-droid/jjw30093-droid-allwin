/**
 * 服务端「关注比赛」(/api/v1/favorites)的唯一前端状态入口。
 *
 * - loadFavorites():模块级 memo,同一标签页内只打一次网络;401 视为匿名
 *   (与 account/page.tsx 的既有约定一致),不再串行先问 /api/v1/me。
 * - 登录态下自动执行 localStorage 一次性迁移(matchesLocal → 服务端):
 *   本地列表本身就是重试队列——只有确认 2xx 的 id 才从 localStorage 移除,
 *   失败的留在原地等下次;后端 INSERT OR IGNORE 幂等,重放无害。
 * - addFavorite/removeFavorite:真实写后端,绝不本地假成功;调用方(
 *   FollowButton)必须等这里成功返回后才翻转 UI。
 * - resetFavoritesCache():登出或任何 mutation 收到 401 时调用,让下一次
 *   loadFavorites() 重新判定登录态(自愈跨标签页登出)。
 *
 * 类型从 OpenAPI 生成类型派生(宪法 §10.3,不手写 DTO)。
 */

import { ApiError, clientFetch, type GetJson } from "@/lib/api-v1";
import { getFollowedMatchIds, removeFollowedMatches } from "@/lib/followed-matches";

type FavoritesResponse = GetJson<"/api/v1/favorites">;

export type FavoritesState =
  | { authenticated: false }
  | { authenticated: true; ids: number[] };

let cache: Promise<FavoritesState> | null = null;

export function loadFavorites(): Promise<FavoritesState> {
  if (!cache) {
    const p = fetchAndMigrate();
    cache = p;
    // 网络/服务器错误不缓存——否则一次瞬时失败会把整个标签页钉死在错误态
    p.catch(() => {
      if (cache === p) cache = null;
    });
  }
  return cache;
}

export function resetFavoritesCache(): void {
  cache = null;
}

async function fetchAndMigrate(): Promise<FavoritesState> {
  let resp: FavoritesResponse;
  try {
    resp = await clientFetch<FavoritesResponse>("/api/v1/favorites");
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return { authenticated: false };
    throw e;
  }
  const serverIds = resp.favorites.map((f) => f.match_id);
  const migrated = await migrateLocalFollows();
  // 本地迁移来的(用户最近的操作)排前;去重保序
  const ids = [...new Set([...migrated, ...serverIds])];
  return { authenticated: true, ids };
}

/**
 * 把匿名期存在 localStorage 的关注迁移到服务端。返回成功迁移的 id
 * (本地存储顺序,最近关注在前)。
 *
 * 硬约束(否则会造成静默数据丢失):
 * - 先 POST、确认 2xx 后才从 localStorage 移除这一条;
 * - 倒序串行(最旧先 POST),让服务端 created_at 顺序 ≈ 本地"最近在前";
 * - 收到 401 整体中止且不再删任何条目(会话中途失效,下次登录重试);
 * - 非 401 的单条失败跳过该条继续,失败条留在本地即自动重试队列。
 */
async function migrateLocalFollows(): Promise<number[]> {
  const local = getFollowedMatchIds(); // 最近关注在前
  if (local.length === 0) return [];
  const migrated: number[] = [];
  for (const id of [...local].reverse()) {
    try {
      await clientFetch("/api/v1/favorites", { method: "POST", body: { match_id: id } });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) break;
      continue;
    }
    removeFollowedMatches([id]);
    migrated.unshift(id); // 恢复"最近在前"
  }
  return migrated;
}

/** 已解析缓存的原位更新——mutation 成功后让同页其它读取方看到新状态。 */
function patchCache(fn: (ids: number[]) => number[]): void {
  const current = cache;
  if (!current) return;
  cache = current.then((s) => (s.authenticated ? { ...s, ids: fn(s.ids) } : s));
}

export async function addFavorite(matchId: number): Promise<void> {
  try {
    await clientFetch("/api/v1/favorites", { method: "POST", body: { match_id: matchId } });
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) resetFavoritesCache();
    throw e;
  }
  patchCache((ids) => (ids.includes(matchId) ? ids : [matchId, ...ids]));
}

export async function removeFavorite(matchId: number): Promise<void> {
  try {
    await clientFetch(`/api/v1/favorites/${matchId}`, { method: "DELETE" });
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) resetFavoritesCache();
    throw e;
  }
  patchCache((ids) => ids.filter((v) => v !== matchId));
}
