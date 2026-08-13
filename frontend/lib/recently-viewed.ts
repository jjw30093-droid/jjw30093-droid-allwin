/**
 * 「最近浏览」首版:纯浏览器本地(localStorage),与关注列表
 * (lib/followed-matches.ts)同一套思路——用户回到网站能直接继续看
 * 昨天研究过的比赛,不做账号级同步、不做个性化推荐。
 */

const KEY = "allwin-recently-viewed";
const MAX_RECENT = 10;

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

export function getRecentlyViewedIds(): number[] {
  return safeRead();
}

export function recordViewed(matchId: number): void {
  const next = [matchId, ...safeRead().filter((v) => v !== matchId)].slice(0, MAX_RECENT);
  try {
    window.localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // 隐私模式等写入失败:静默
  }
}
