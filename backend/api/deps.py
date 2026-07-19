"""FastAPI 依赖:请求级连接、当前用户、entitlement 上下文、CSRF/Origin 校验。

门禁真源在服务端(CLAUDE.md §8.3):所有 v1 端点经 AuthContext 做字段级投影,
绝不下发后再遮挡。
"""

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from backend.auth import service
from backend.auth.config import AuthSettings
from backend.auth.entitlements import resolve_entitlements
from backend.db.connections import connect_ro, connect_rw
from backend.db.util import utc_now_iso

NO_STORE = "private, no-store"


def get_settings(request: Request) -> AuthSettings:
    return request.app.state.auth_settings


def get_provider(request: Request):
    return request.app.state.wechat_provider


def platform_ro():
    conn = connect_ro("platform")
    try:
        yield conn
    finally:
        conn.close()


def platform_rw():
    conn = connect_rw("platform")
    try:
        yield conn
    finally:
        conn.close()


def core_ro():
    conn = connect_ro("core")
    try:
        yield conn
    finally:
        conn.close()


def odds_ro():
    conn = connect_ro("odds")
    try:
        yield conn
    finally:
        conn.close()


@dataclass(frozen=True)
class AuthContext:
    user_id: str | None
    role: str | None
    display_name: str | None
    session_id: str | None
    session_row: object          # sqlite3.Row | None(CSRF 校验用)
    plan_id: str
    entitlements: frozenset

    @property
    def authenticated(self) -> bool:
        return self.user_id is not None

    def has(self, entitlement: str) -> bool:
        return entitlement in self.entitlements


ANONYMOUS_PLAN = "free"


def get_auth_context(request: Request, settings: AuthSettings = Depends(get_settings)) -> AuthContext:
    token = request.cookies.get(settings.cookie_name)
    conn = connect_ro("platform")
    try:
        session_row = service.get_session_by_token(conn, token) if token else None
        if session_row is None:
            plan_id, ents = resolve_entitlements(conn, None, utc_now_iso())
            return AuthContext(None, None, None, None, None, plan_id, ents)
        plan_id, ents = resolve_entitlements(conn, session_row["user_id"], utc_now_iso())
        return AuthContext(
            user_id=session_row["user_id"],
            role=session_row["user_role"],
            display_name=session_row["display_name"],
            session_id=session_row["id"],
            session_row=session_row,
            plan_id=plan_id,
            entitlements=ents,
        )
    finally:
        conn.close()


def require_user(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
    if not ctx.authenticated:
        raise HTTPException(status_code=401, detail="需要登录")
    return ctx


def require_admin(ctx: AuthContext = Depends(require_user)) -> AuthContext:
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return ctx


def require_entitlement(entitlement: str):
    def _dep(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if not ctx.has(entitlement):
            raise HTTPException(
                status_code=403 if ctx.authenticated else 401,
                detail={"code": "membership_required", "entitlement": entitlement},
            )
        return ctx

    return _dep


def _origin_allowed(request: Request, settings: AuthSettings) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        referer = request.headers.get("referer")
        if referer is None:
            return False
        origin = referer
    allowed = set(settings.allowed_origins) | {settings.public_base_url}
    return any(origin == o or origin.startswith(o + "/") for o in allowed)


def require_csrf(
    request: Request,
    ctx: AuthContext = Depends(require_user),
    settings: AuthSettings = Depends(get_settings),
) -> AuthContext:
    """写请求防护:Origin/Referer allowlist + 双提交 CSRF token(CLAUDE.md §7.4)。"""
    if not _origin_allowed(request, settings):
        raise HTTPException(status_code=403, detail="Origin 校验失败")
    csrf = request.headers.get("x-csrf-token")
    if not service.verify_csrf(ctx.session_row, csrf):
        raise HTTPException(status_code=403, detail="CSRF 校验失败")
    return ctx


def auth_context_for(request: Request) -> AuthContext:
    """非依赖注入场景(legacy /api/league 兼容层)用的直接解析入口。"""
    settings = getattr(request.app.state, "auth_settings", None)
    if settings is None:
        from backend.auth.config import load_auth_settings

        settings = load_auth_settings()
    return get_auth_context(request, settings)


def client_ip_key(request: Request) -> str:
    # 单机部署经 Cloudflare/Nginx 回源;真实 IP 由反代头传递,仅作限流键,不落库
    return (
        request.headers.get("cf-connecting-ip")
        or request.headers.get("x-real-ip")
        or (request.client.host if request.client else "unknown")
    )
