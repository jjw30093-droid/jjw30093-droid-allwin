/**
 * 「关注比赛」首版:纯浏览器本地(localStorage),不写任何账号数据。
 * 已登录用户的服务端收藏(/api/v1/favorites)与此互不影响;后续如需合并,
 * 以服务端收藏为准迁移,本模块只做低成本的"稍后看"。
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
