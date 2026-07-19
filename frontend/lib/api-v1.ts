/**
 * /api/v1 统一客户端(服务端 RSC 与浏览器共用)。
 *
 * 类型来自 OpenAPI 生成的 lib/api-types.ts(Pydantic 单一真源;npm run gen:api 重新生成)。
 * 会话 cookie Path=/api/v1(Next 服务端读不到)——会员数据一律由浏览器带
 * credentials 调私有 API,公共 HTML 不因登录态变化(宪法 §10.2)。
 */

import type { paths } from "./api-types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

/* ── 从生成的 paths 派生响应类型 ───────────────────────── */

type JsonOf<T> = T extends {
  responses: { 200: { content: { "application/json": infer J } } };
}
  ? J
  : never;

export type GetJson<P extends keyof paths> = paths[P] extends { get: infer G }
  ? JsonOf<G>
  : never;
export type PostJson<P extends keyof paths> = paths[P] extends { post: infer G }
  ? JsonOf<G>
  : never;

export type MeResponse = GetJson<"/api/v1/me">;
export type LeagueInfo = GetJson<"/api/v1/leagues">[number];
export type MatchListResponse = GetJson<"/api/v1/matches">;
export type MatchSummary = MatchListResponse["matches"][number];
export type MatchDetailResponse = GetJson<"/api/v1/matches/{match_id}">;
export type PredictionResponse = GetJson<"/api/v1/matches/{match_id}/prediction">;
export type TrackRecordResponse = GetJson<"/api/v1/track-record">;

/* ── 服务端(RSC)读取:匿名公开数据 ───────────────────── */

export async function serverGet<T>(
  path: string,
  opts: { revalidate?: number } = {},
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...(opts.revalidate != null
      ? { next: { revalidate: opts.revalidate } }
      : { cache: "no-store" as const }),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`serving API ${res.status}: ${body.slice(0, 300)}`);
  }
  return res.json() as Promise<T>;
}

/** 401/403(联赛门禁等)返回 null 而不是抛错,页面渲染引导态。 */
export async function serverGetOptional<T>(
  path: string,
  opts: { revalidate?: number } = {},
): Promise<T | null> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...(opts.revalidate != null
      ? { next: { revalidate: opts.revalidate } }
      : { cache: "no-store" as const }),
  });
  if (res.status === 401 || res.status === 403 || res.status === 404) return null;
  if (!res.ok) throw new Error(`serving API ${res.status}`);
  return res.json() as Promise<T>;
}

/* ── 浏览器端:带会话 cookie 的私有请求 ────────────────── */

export function readCsrfToken(): string {
  if (typeof document === "undefined") return "";
  const m = document.cookie.match(/(?:^|;\s*)allwin_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(`API ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

export async function clientFetch<T>(
  path: string,
  opts: { method?: "GET" | "POST" | "DELETE"; body?: unknown } = {},
): Promise<T> {
  const method = opts.method ?? "GET";
  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRF-Token"] = readCsrfToken();
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    credentials: "include",
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 204) return undefined as T;
  const isJson = res.headers.get("content-type")?.includes("application/json");
  const body = isJson ? await res.json() : await res.text();
  if (!res.ok) throw new ApiError(res.status, isJson ? (body as { detail?: unknown }).detail : body);
  return body as T;
}

/* ── 常用调用 ──────────────────────────────────────────── */

export const getMe = () => clientFetch<MeResponse>("/api/v1/me");
export const logout = () => clientFetch("/api/v1/auth/logout", { method: "POST" });

export const createDeviceLogin = () =>
  clientFetch<{ request_id: string; secret: string; qr_url: string; expires_at: string }>(
    "/api/v1/auth/wechat/device",
    { method: "POST", body: {} },
  );
export const claimDeviceLogin = (requestId: string, secret: string) =>
  clientFetch<{ status: string }>(`/api/v1/auth/wechat/device/${requestId}/claim`, {
    method: "POST",
    body: { secret },
  });

export const redeemCode = (code: string) =>
  clientFetch<{ status: string; plan_id: string; ends_at: string }>("/api/v1/redeem", {
    method: "POST",
    body: { code },
  });

/** 登录入口 URL(必须由用户点击触发跳转,不得自动跳)。 */
export const wechatLoginUrl = (next: string) =>
  `${API_BASE}/api/v1/auth/wechat/oa/start?next=${encodeURIComponent(next)}`;
