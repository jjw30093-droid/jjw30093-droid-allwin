/**
 * first-party 最小埋点(宪法 P0.10):匿名随机 id,不含 OpenID/IP/token。
 * 失败静默——埋点绝不影响用户体验。
 */

import { API_BASE } from "./api-v1";

const ANON_KEY = "allwin_anon_id";

function anonId(): string {
  if (typeof window === "undefined") return "";
  let id = window.localStorage.getItem(ANON_KEY);
  if (!id) {
    id = crypto.randomUUID();
    window.localStorage.setItem(ANON_KEY, id);
  }
  return id;
}

export type AnalyticsEvent =
  | "landing_view"
  | "match_view"
  | "login_started"
  | "login_completed"
  | "pricing_viewed"
  | "favorite_added"
  | "studio_exported";

export function track(event: AnalyticsEvent, meta: Record<string, unknown> = {}): void {
  if (typeof window === "undefined") return;
  const payload = JSON.stringify({
    event,
    anon_id: anonId(),
    path: window.location.pathname,
    referrer: document.referrer || null,
    meta,
  });
  try {
    fetch(`${API_BASE}/api/v1/analytics/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: payload,
      keepalive: true,
    }).catch(() => {});
  } catch {
    /* 静默 */
  }
}
