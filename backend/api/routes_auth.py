"""/api/v1/auth/* 与 /api/v1/me。

登录路线(2026-08,CLAUDE.md §7.3 修改已获用户批准):带参数二维码 + webhook 事件。
网页授权(snsapi_base)端点已移除——网页授权域名要求 ICP 备案,本项目部署 AWS 东京
且不迁回大陆备案。流程与安全设计见 docs/auth-wechat.md;全部响应 private, no-store。
"""

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from backend.auth import service, wechat_webhook
from backend.auth.config import AuthSettings
from backend.auth.providers import AuthProviderError
from backend.db.connections import tx

from .schemas import (
    ApiErrorDTO,
    DeviceClaimResultDTO,
    DeviceLoginCreatedDTO,
    MeDTO,
    OkDTO,
    error_responses,
)
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

router = APIRouter(
    prefix="/api/v1",
    tags=["auth"],
    responses=error_responses(400, 401, 403, 410, 422, 429, 502),
)

# 微信端点专用:WECHAT_AUTH_ENABLED=0 时统一 503,顶层结构与全站一致(ApiErrorDTO)
AUTH_DISABLED_RESPONSE = {
    503: {"model": ApiErrorDTO, "description": "微信登录暂未开放(AUTH_DISABLED)"}
}


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = NO_STORE


class WechatDisabledException(Exception):
    """认证三态(CLAUDE.md §7.3):WECHAT_AUTH_ENABLED=0(real)时微信端点统一
    503 + 全站统一错误顶层结构 {"code","message","details"}。app.py 注册 handler。"""

    body = {"code": "AUTH_DISABLED", "message": "微信登录暂未开放", "details": None}

    def to_response(self) -> JSONResponse:
        resp = JSONResponse(self.body, status_code=503)
        _no_store(resp)
        return resp


def _ensure_wechat_enabled(settings: AuthSettings) -> None:
    """微信相关端点(device、claim、webhook)可用性闸门。

    mock(仅 development)视为可用,便于本地 E2E;real 必须显式 WECHAT_AUTH_ENABLED=1;
    密码登录/登出/me 不经过此闸门,不受影响。

    注意副作用(docs/auth-wechat.md §6):公众号后台一旦启用「服务器配置」,微信会把
    所有用户消息推到 webhook;本站在 AUTH_DISABLED 状态下对 POST 回 503,微信侧会向
    发消息的用户显示"该公众号暂时无法提供服务"——这是关闭态的如实行为,不是故障。
    """
    if not settings.wechat_login_available:
        raise WechatDisabledException()


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


# ── 可用登录方式(登录页据此显示"微信登录暂未开放") ────────

class AuthMethodsDTO(BaseModel):
    wechat_enabled: bool


@router.get("/auth/methods", response_model=AuthMethodsDTO)
def auth_methods(
    response: Response,
    settings: AuthSettings = Depends(get_settings),
):
    _no_store(response)
    return {"wechat_enabled": settings.wechat_login_available}


# ── 扫码登录:创建请求 + 带参二维码 ────────────────────────

@router.post(
    "/auth/wechat/device",
    response_model=DeviceLoginCreatedDTO,
    responses=AUTH_DISABLED_RESPONSE,
)
def create_device_login(
    request: Request,
    settings: AuthSettings = Depends(get_settings),
    provider=Depends(get_provider),
    conn=Depends(platform_rw),
):
    """浏览器发起扫码登录:创建一次性 request,并向微信申请带参二维码
    (scene_str = 公开 request id;secret 只回给浏览器,绝不进二维码)。"""
    _ensure_wechat_enabled(settings)
    if not limiter.allow(f"device_create:{client_ip_key(request)}", 10, 60):
        raise HTTPException(status_code=429, detail="请求过于频繁")
    with tx(conn):
        req = service.create_device_request(conn, ttl_seconds=settings.device_request_ttl_seconds)
    try:
        # 网络调用不在事务内(§5.3 短事务);QR 有效期与 request 对齐
        qr = provider.create_login_qrcode(
            conn, scene_str=req["request_id"],
            expire_seconds=settings.device_request_ttl_seconds,
        )
    except AuthProviderError as e:
        # 失败的 request 留给 TTL 自然过期(无 QR 指向它,无法被批准,无害)
        if e.errcode == 48001:
            log.error("带参二维码接口无权限(errcode=48001,常见于公众号年审过期)")
        else:
            log.warning("创建带参二维码失败: %s", e)
        raise HTTPException(status_code=502, detail="微信扫码服务暂时不可用,请稍后重试")
    with tx(conn):
        service.attach_qr_to_device_request(conn, req["request_id"], qr.ticket, qr.url)
    resp = JSONResponse(
        {
            "request_id": req["request_id"],
            "secret": req["secret"],
            "qr_url": qr.url,
            "expires_at": req["expires_at"],
        }
    )
    _no_store(resp)
    return resp


class DeviceClaimBody(BaseModel):
    secret: str


@router.post(
    "/auth/wechat/device/{request_id}/claim",
    response_model=DeviceClaimResultDTO,
    responses=AUTH_DISABLED_RESPONSE,
)
def claim_device_login(
    request_id: str,
    body: DeviceClaimBody,
    request: Request,
    settings: AuthSettings = Depends(get_settings),
    conn=Depends(platform_rw),
):
    _ensure_wechat_enabled(settings)
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


# ── 微信消息推送 webhook(服务器对服务器,无 Cookie/CSRF) ──

WEBHOOK_PLAIN_RESPONSES = {
    200: {"content": {"text/plain": {}}, "description": "校验回显 echostr(text/plain)"},
    403: {"model": ApiErrorDTO, "description": "签名校验失败"},
    **AUTH_DISABLED_RESPONSE,
}


def _verify_webhook_signature(
    settings: AuthSettings, signature: str, timestamp: str, nonce: str
) -> None:
    if not wechat_webhook.verify_signature(
        settings.wechat_webhook_token, timestamp, nonce, signature
    ):
        raise HTTPException(status_code=403, detail="签名校验失败")


@router.get(
    "/auth/wechat/webhook",
    response_class=PlainTextResponse,
    responses=WEBHOOK_PLAIN_RESPONSES,
)
def wechat_webhook_verify(
    signature: str = "",
    timestamp: str = "",
    nonce: str = "",
    echostr: str = "",
    settings: AuthSettings = Depends(get_settings),
):
    """公众号后台「服务器配置」保存时的一次性校验握手:验签通过原样回显 echostr。"""
    _ensure_wechat_enabled(settings)
    _verify_webhook_signature(settings, signature, timestamp, nonce)
    resp = PlainTextResponse(echostr)
    _no_store(resp)
    return resp


@router.post(
    "/auth/wechat/webhook",
    response_class=PlainTextResponse,
    responses={
        200: {
            "content": {"application/xml": {}, "text/plain": {}},
            "description": "被动回复 XML,或 success(text/plain)",
        },
        403: {"model": ApiErrorDTO, "description": "签名校验失败/时间戳过期"},
        **AUTH_DISABLED_RESPONSE,
    },
)
async def wechat_webhook_events(
    request: Request,
    signature: str = "",
    timestamp: str = "",
    nonce: str = "",
    settings: AuthSettings = Depends(get_settings),
    provider=Depends(get_provider),
    conn=Depends(platform_rw),
):
    """微信服务器推送的事件入口。登录相关:SCAN / subscribe(带 qrscene_ 场景值)。

    安全:共享 Token 签名 + 时间戳 ±300s + nonce 一次性(重放静默回 success,
    因为微信 5 秒未收到应答会原样重试,不能把重试当攻击)。5 秒内必须应答,
    处理只涉本地 DB,无外呼。
    """
    _ensure_wechat_enabled(settings)
    _verify_webhook_signature(settings, signature, timestamp, nonce)
    if not wechat_webhook.timestamp_fresh(timestamp, int(time.time())):
        raise HTTPException(status_code=403, detail="时间戳超出允许窗口")

    body = await request.body()
    if len(body) > wechat_webhook.MAX_BODY_BYTES:
        raise HTTPException(status_code=400, detail="body 过大")

    with tx(conn):
        first_seen = wechat_webhook.register_nonce(conn, nonce)
    if not first_seen:
        # 微信重试(同一 nonce):第一次已处理,直接确认
        resp = PlainTextResponse("success")
        _no_store(resp)
        return resp

    try:
        event = wechat_webhook.parse_event_xml(body)
    except wechat_webhook.WebhookParseError as e:
        # 非法/非预期负载:确认掉,避免微信重试风暴;只留服务端日志
        log.warning("webhook 负载解析失败: %s", e)
        resp = PlainTextResponse("success")
        _no_store(resp)
        return resp

    reply_text: str | None = None
    if event.scene_str:
        reply_text = _handle_login_scan(conn, provider, event)
    # 其余事件/普通消息:本轮不处理(OTP 第二通道是下一轮独立课题)

    if reply_text is not None:
        resp = Response(
            content=wechat_webhook.build_text_reply(event, reply_text, int(time.time())),
            media_type="application/xml",
        )
    else:
        resp = PlainTextResponse("success")
    _no_store(resp)
    return resp


def _handle_login_scan(conn, provider, event: wechat_webhook.WechatEvent) -> str:
    """按 scene_str(= device request id)批准登录请求,返回给用户的被动回复文案。"""
    row = service.get_device_request(conn, event.scene_str)
    if row is None:
        return "二维码无效,请回到浏览器刷新后重新扫码"

    from backend.db.util import utc_now_iso

    if row["status"] in ("approved", "claimed"):
        # 幂等:重复扫码/微信重投递,不再变更状态
        return "已确认登录,请回到浏览器继续"
    if row["status"] != "pending" or row["expires_at"] <= utc_now_iso():
        return "二维码已过期,请回到浏览器刷新后重新扫码"

    with tx(conn):
        user_id = service.get_or_create_user_by_identity(
            conn,
            provider="wechat_oa",
            provider_app_id=getattr(provider, "app_id", ""),
            provider_subject=event.openid,
        )
        ok = service.approve_device_request(conn, event.scene_str, user_id)
    if not ok:
        return "二维码已过期,请回到浏览器刷新后重新扫码"
    log.info("device request %s 已由 webhook 批准", event.scene_str)
    return "登录成功,请回到浏览器继续"


# ── 密码登录(仅 CLI 创建的 admin 账号使用) ─────────────────

class PasswordLoginBody(BaseModel):
    username: str
    password: str


@router.post("/auth/password/login", response_model=OkDTO)
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

@router.post("/auth/logout", response_model=OkDTO)
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
