"""/api/v1/auth/* 与 /api/v1/me。

流程与安全设计见 docs/auth-wechat.md;全部响应 private, no-store。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from backend.auth import service
from backend.auth.config import AuthSettings
from backend.auth.providers import AuthProviderError
from backend.db.connections import tx

from .schemas import MeDTO
from .deps import (
    NO_STORE,
    AuthContext,
    client_ip_key,
    get_auth_context,
    get_provider,
    get_settings,
    platform_rw,
    require_csrf,
)
from .ratelimit import limiter

log = logging.getLogger("allwin.auth")

router = APIRouter(prefix="/api/v1", tags=["auth"])


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = NO_STORE


def _ensure_wechat_enabled(settings: AuthSettings) -> None:
    # mock(仅 development)视为可用,便于本地 E2E;real 必须显式开启
    if settings.wechat_provider_kind == "real" and not settings.wechat_auth_enabled:
        raise HTTPException(status_code=503, detail="微信登录未启用")


def _callback_uri(settings: AuthSettings) -> str:
    return f"{settings.public_base_url}/api/v1/auth/wechat/oa/callback"


def _set_session_cookies(response: Response, settings: AuthSettings, sess: dict) -> None:
    max_age = settings.session_ttl_days * 86400
    response.set_cookie(
        settings.cookie_name,
        sess["token"],
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=settings.cookie_path,     # host-only:不设 domain
    )
    # CSRF 双提交 cookie:JS 需可读,Path=/
    response.set_cookie(
        settings.csrf_cookie_name,
        sess["csrf_token"],
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_session_cookies(response: Response, settings: AuthSettings) -> None:
    response.delete_cookie(settings.cookie_name, path=settings.cookie_path)
    response.delete_cookie(settings.csrf_cookie_name, path="/")


# ── 微信 OAuth(手机 / 微信内) ─────────────────────────────

@router.get("/auth/wechat/oa/start")
def wechat_oa_start(
    request: Request,
    next: str = "/",
    device: str | None = None,
    settings: AuthSettings = Depends(get_settings),
    provider=Depends(get_provider),
    conn=Depends(platform_rw),
):
    """用户点击登录后进入(前端不得在页面加载时自动跳转到这里)。"""
    _ensure_wechat_enabled(settings)
    if not limiter.allow(f"oa_start:{client_ip_key(request)}", 20, 60):
        raise HTTPException(status_code=429, detail="请求过于频繁")
    if not service.is_safe_next_path(next):
        next = "/"

    kind = "login"
    if device is not None:
        row = conn.execute(
            "SELECT status, expires_at FROM device_login_requests WHERE id=?", (device,)
        ).fetchone()
        from backend.db.util import utc_now_iso

        if row is None or row["status"] != "pending" or row["expires_at"] <= utc_now_iso():
            raise HTTPException(status_code=410, detail="扫码请求已失效,请回到电脑刷新二维码")
        kind = "device_approve"

    with tx(conn):
        state = service.create_oauth_state(
            conn, kind=kind, next_path=next,
            ttl_seconds=settings.oauth_state_ttl_seconds,
            device_request_id=device,
        )
    url = provider.authorize_url(_callback_uri(settings), state)
    resp = RedirectResponse(url, status_code=302)
    _no_store(resp)
    return resp


@router.get("/auth/wechat/oa/callback")
def wechat_oa_callback(
    request: Request,
    code: str = "",
    state: str = "",
    settings: AuthSettings = Depends(get_settings),
    provider=Depends(get_provider),
    conn=Depends(platform_rw),
):
    _ensure_wechat_enabled(settings)
    with tx(conn):
        state_row = service.consume_oauth_state(conn, state)
    if state_row is None:
        # state 无效/过期/重放:一律同一响应,不泄露区分
        raise HTTPException(status_code=400, detail="登录状态无效或已过期,请重新发起登录")

    try:
        identity = provider.exchange_code(code)
    except AuthProviderError:
        log.warning("wechat code exchange failed (state kind=%s)", state_row["kind"])
        raise HTTPException(status_code=502, detail="微信登录暂时不可用,请稍后重试")

    with tx(conn):
        user_id = service.get_or_create_user_by_identity(
            conn,
            provider="wechat_oa",
            provider_app_id=getattr(provider, "app_id", ""),
            provider_subject=identity.openid,
            union_id=identity.union_id,
        )

    if state_row["kind"] == "device_approve":
        with tx(conn):
            ok = service.approve_device_request(conn, state_row["device_request_id"], user_id)
        if not ok:
            raise HTTPException(status_code=410, detail="扫码请求已失效,请回到电脑刷新二维码")
        resp = JSONResponse({"status": "approved", "message": "已批准登录,请回到电脑继续"})
        _no_store(resp)
        return resp

    # kind == login:创建会话,种 Cookie,跳回 next
    with tx(conn):
        sess = service.create_session(
            conn, user_id,
            ttl_days=settings.session_ttl_days,
            user_agent=request.headers.get("user-agent"),
        )
    next_path = state_row["next_path"] if service.is_safe_next_path(state_row["next_path"]) else "/"
    resp = RedirectResponse(f"{settings.frontend_base_url}{next_path}", status_code=302)
    _set_session_cookies(resp, settings, sess)
    _no_store(resp)
    return resp


# ── Device Login(电脑扫码) ───────────────────────────────

@router.post("/auth/wechat/device")
def create_device_login(
    request: Request,
    settings: AuthSettings = Depends(get_settings),
    conn=Depends(platform_rw),
):
    _ensure_wechat_enabled(settings)
    if not limiter.allow(f"device_create:{client_ip_key(request)}", 10, 60):
        raise HTTPException(status_code=429, detail="请求过于频繁")
    with tx(conn):
        req = service.create_device_request(conn, ttl_seconds=settings.device_request_ttl_seconds)
    # 二维码只含公开 request id;secret 只留在浏览器内存
    qr_url = f"{settings.public_base_url}/api/v1/auth/wechat/oa/start?device={req['request_id']}"
    resp = JSONResponse(
        {
            "request_id": req["request_id"],
            "secret": req["secret"],
            "qr_url": qr_url,
            "expires_at": req["expires_at"],
        }
    )
    _no_store(resp)
    return resp


class DeviceClaimBody(BaseModel):
    secret: str


@router.post("/auth/wechat/device/{request_id}/claim")
def claim_device_login(
    request_id: str,
    body: DeviceClaimBody,
    request: Request,
    settings: AuthSettings = Depends(get_settings),
    conn=Depends(platform_rw),
):
    if not limiter.allow(f"device_claim:{client_ip_key(request)}", 60, 60):
        raise HTTPException(status_code=429, detail="请求过于频繁")
    with tx(conn):
        status, user_id = service.claim_device_request(conn, request_id, body.secret)
    if status == "pending":
        resp = JSONResponse({"status": "pending"})
        _no_store(resp)
        return resp
    if status == "forbidden":
        raise HTTPException(status_code=403, detail="secret 校验失败")
    if status in ("expired", "gone"):
        raise HTTPException(status_code=410, detail="扫码请求已失效")
    # claimed:原子领取成功,创建会话
    with tx(conn):
        sess = service.create_session(
            conn, user_id,
            ttl_days=settings.session_ttl_days,
            user_agent=request.headers.get("user-agent"),
        )
    resp = JSONResponse({"status": "claimed"})
    _set_session_cookies(resp, settings, sess)
    _no_store(resp)
    return resp


# ── 密码登录(仅 CLI 创建的 admin 账号使用) ─────────────────

class PasswordLoginBody(BaseModel):
    username: str
    password: str


@router.post("/auth/password/login")
def password_login(
    body: PasswordLoginBody,
    request: Request,
    settings: AuthSettings = Depends(get_settings),
    conn=Depends(platform_rw),
):
    if not limiter.allow(f"pw_login:{client_ip_key(request)}", 5, 60):
        raise HTTPException(status_code=429, detail="尝试过于频繁,请稍后再试")
    row = conn.execute(
        """SELECT u.id, u.password_hash, u.status FROM auth_identities ai
           JOIN users u ON u.id = ai.user_id
           WHERE ai.provider='password' AND ai.provider_subject=?""",
        (body.username,),
    ).fetchone()
    generic = HTTPException(status_code=401, detail="用户名或密码错误")
    if row is None or not row["password_hash"] or row["status"] != "active":
        raise generic
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError

    try:
        PasswordHasher().verify(row["password_hash"], body.password)
    except VerifyMismatchError:
        raise generic
    with tx(conn):
        sess = service.create_session(
            conn, row["id"],
            ttl_days=settings.session_ttl_days,
            user_agent=request.headers.get("user-agent"),
        )
    resp = JSONResponse({"status": "ok"})
    _set_session_cookies(resp, settings, sess)
    _no_store(resp)
    return resp


# ── 会话 ───────────────────────────────────────────────────

@router.post("/auth/logout")
def logout(
    response: Response,
    ctx: AuthContext = Depends(require_csrf),
    settings: AuthSettings = Depends(get_settings),
    conn=Depends(platform_rw),
):
    with tx(conn):
        service.revoke_session(conn, ctx.session_id)
    resp = JSONResponse({"status": "ok"})
    _clear_session_cookies(resp, settings)
    _no_store(resp)
    return resp


@router.get("/me", response_model=MeDTO)
def me(response: Response, ctx: AuthContext = Depends(get_auth_context)):
    _no_store(response)
    if not ctx.authenticated:
        return {
            "authenticated": False,
            "user": None,
            "plan": ctx.plan_id,
            "entitlements": sorted(ctx.entitlements),
        }
    return {
        "authenticated": True,
        "user": {"id": ctx.user_id, "display_name": ctx.display_name, "role": ctx.role},
        "plan": ctx.plan_id,
        "entitlements": sorted(ctx.entitlements),
        "session_expires_at": ctx.session_row["expires_at"],
    }
