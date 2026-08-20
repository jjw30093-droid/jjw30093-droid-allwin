/**
 * 「关注比赛」的浏览器本地列表:匿名用户的关注存这里;登录后由
 * lib/favorites.ts 迁移到服务端(/api/v1/favorites),迁移只删除已确认
 * 写入成功的 id——本地列表同时充当迁移失败时的重试队列,绝不先删后写。
 */

const KEY = "allwin-followed-matches";
const MAX_FOLLOWED = 20;

function safeRead(): number[] {
  if (typeof window === "undefined") return [];
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(KEY) ?? "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((v): v is number => typeof v === "number" && Number.isInteger(v));
  } catch {
    return [];
  }
}

export function getFollowedMatchIds(): number[] {
  return safeRead();
}

export function isFollowed(matchId: number): boolean {
  return safeRead().includes(matchId);
}

/** 迁移成功后移除对应 id;列表清空时整个 key 删掉。写失败静默(隐私模式)。 */
export function removeFollowedMatches(ids: number[]): void {
  if (ids.length === 0) return;
  const next = safeRead().filter((v) => !ids.includes(v));
  try {
    if (next.length === 0) window.localStorage.removeItem(KEY);
    else window.localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // 隐私模式等写入失败:静默,不阻塞页面
  }
}

/** 返回切换后的关注状态(true=已关注)。 */
export function toggleFollowed(matchId: number): boolean {
  const ids = safeRead();
  const idx = ids.indexOf(matchId);
  let next: number[];
  let followed: boolean;
  if (idx >= 0) {
    next = ids.filter((v) => v !== matchId);
    followed = false;
  } else {
    next = [matchId, ...ids].slice(0, MAX_FOLLOWED);
    followed = true;
  }
  try {
    window.localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // 隐私模式等写入失败:静默,不阻塞页面
  }
  return followed;
}
