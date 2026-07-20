/**
 * /api/v1 统一客户端(服务端 RSC 与浏览器共用)。
 *
 * 类型来自 OpenAPI 生成的 lib/api-types.ts(Pydantic 单一真源;npm run gen:api 重新生成)。
 * 会话 cookie Path=/api/v1(Next 服务端读不到)——会员数据一律由浏览器带
 * credentials 调私有 API,公共 HTML 不因登录态变化(宪法 §10.2)。
 * 基址统一来自 lib/api-base.ts(单一真源,宪法 §10.3),本文件不再自行解析 env。
 */

import { clientApiBase, serverApiBase } from "./api-base";
import type { components, paths } from "./api-types";

/**
 * 浏览器语义基址(保留原导出名):development/E2E = NEXT_PUBLIC_API_BASE,
 * production = ""(同源相对,/api/v1 → Cloudflare → Nginx)。只用于浏览器发起的
 * fetch 与写进 HTML 的 href;服务端(RSC)取数用 serverGet(内部走 serverApiBase,
 * 可被 INTERNAL_API_BASE 运行期覆盖)。
 */
export const API_BASE = clientApiBase();

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
export type AuthMethodsResponse = GetJson<"/api/v1/auth/methods">;
export type PasswordLoginResponse = PostJson<"/api/v1/auth/password/login">;

/* ── 服务端(RSC)读取:匿名公开数据 ───────────────────── */

export async function serverGet<T>(
  path: string,
  opts: { revalidate?: number } = {},
): Promise<T> {
  const res = await fetch(`${serverApiBase()}${path}`, {
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
  const res = await fetch(`${serverApiBase()}${path}`, {
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

/* ── 全站统一错误契约(CLAUDE.md §10):{code, message, details} ─────────
 * 后端 backend.api.error_handlers 把所有非 2xx JSON 响应(HTTPException/422
 * 校验失败/未捕获异常/未知路由等)都归一成这一个顶层结构;前端只需认这一种
 * 形状,不再有 `.detail` 这个字段。 */

export type ApiErrorBody = components["schemas"]["ApiErrorDTO"];

const FALLBACK_MESSAGE_BY_STATUS: Record<number, string> = {
  400: "请求参数有误",
  401: "需要登录",
  403: "权限不足",
  404: "资源不存在",
  409: "请求存在冲突",
  410: "资源已失效",
  422: "请求参数校验失败",
  429: "请求过于频繁,请稍后再试",
  500: "服务器内部错误",
  502: "上游服务暂时不可用",
  503: "服务暂不可用",
};

function fallbackErrorBody(status: number): ApiErrorBody {
  return {
    code: `HTTP_${status}`,
    message: FALLBACK_MESSAGE_BY_STATUS[status] ?? "请求失败",
    details: null,
  };
}

/** 非 JSON、空 body、畸形 JSON 时的安全回退:code 按状态码,details=null,
 * 绝不再次抛出(如 JSON.parse 的 SyntaxError)。 */
function parseErrorBody(status: number, raw: unknown): ApiErrorBody {
  if (
    raw !== null &&
    typeof raw === "object" &&
    typeof (raw as Record<string, unknown>).code === "string" &&
    typeof (raw as Record<string, unknown>).message === "string"
  ) {
    const r = raw as Record<string, unknown>;
    const details = r.details;
    return {
      code: r.code as string,
      message: r.message as string,
      details: details && typeof details === "object" ? (details as ApiErrorBody["details"]) : null,
    };
  }
  return fallbackErrorBody(status);
}

async function safeParseJsonError(res: Response): Promise<unknown> {
  const isJson = res.headers.get("content-type")?.includes("application/json");
  if (!isJson) return null;
  try {
    return await res.json();
  } catch {
    return null; // 空 body / 畸形 JSON:安全回退,不让解析失败本身再抛错
  }
}

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown> | null;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.status = status;
    this.code = body.code;
    this.details = (body.details as Record<string, unknown> | null | undefined) ?? null;
  }
}

/** 统一的错误展示文案:优先用服务端 message,取不到时用调用方提供的 fallback。 */
export function apiErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    return error.message || fallback;
  }
  return fallback;
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
  if (!res.ok) {
    const raw = await safeParseJsonError(res);
    throw new ApiError(res.status, parseErrorBody(res.status, raw));
  }
  const isJson = res.headers.get("content-type")?.includes("application/json");
  return (isJson ? await res.json() : await res.text()) as T;
}

/* ── 常用调用 ──────────────────────────────────────────── */

export const getMe = () => clientFetch<MeResponse>("/api/v1/me");
export const logout = () => clientFetch("/api/v1/auth/logout", { method: "POST" });

export const createDeviceLogin = () =>
  clientFetch<PostJson<"/api/v1/auth/wechat/device">>("/api/v1/auth/wechat/device", {
    method: "POST",
    body: {},
  });
export const claimDeviceLogin = (requestId: string, secret: string) =>
  clientFetch<PostJson<"/api/v1/auth/wechat/device/{request_id}/claim">>(
    `/api/v1/auth/wechat/device/${requestId}/claim`,
    { method: "POST", body: { secret } },
  );

export const redeemCode = (code: string) =>
  clientFetch<PostJson<"/api/v1/redeem">>("/api/v1/redeem", {
    method: "POST",
    body: { code },
  });

/** 登录入口 URL(必须由用户点击触发跳转,不得自动跳)。 */
export const wechatLoginUrl = (next: string) =>
  `${API_BASE}/api/v1/auth/wechat/oa/start?next=${encodeURIComponent(next)}`;
