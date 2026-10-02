/**
 * 登录门禁的前端侧(2026-10-02,经站长批准):未登录只能看英超的比赛详情与
 * 联赛数据,其他联赛的数据端点返回 401 `login_required`,页面据此直接跳到
 * 登录页,登录后回到原页面。后端是权限真源(backend/api/data_access.py),
 * 这里只负责体验。
 */

import { ApiError } from "./api-v1";

/** 后端单一真源 backend/queries/leagues.py::ANON_LEAGUE_IDS 的前端镜像
 *  (tests/login-gate.test.ts 读后端源码校验两边一致)。 */
export const ANON_LEAGUE_IDS: ReadonlySet<number> = new Set([47]);

export function isAnonLeague(leagueId: number | string): boolean {
  return ANON_LEAGUE_IDS.has(Number(leagueId));
}

export function isLoginRequired(e: unknown): boolean {
  return e instanceof ApiError && e.status === 401;
}

export function loginHref(nextPath: string): string {
  return `/login?next=${encodeURIComponent(nextPath)}`;
}

/** 用 replace 而不是 assign:登录页点返回回到列表,不会又弹回这个被挡的页面。 */
export function redirectToLogin(): void {
  window.location.replace(loginHref(window.location.pathname + window.location.search));
}
