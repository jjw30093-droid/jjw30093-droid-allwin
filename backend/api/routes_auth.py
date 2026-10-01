"""/api/v1/auth/* 与 /api/v1/me。

登录路线(2026-10,CLAUDE.md §7.3 修改经站长批准):公众号发码登录——网页显示 4 位验证码,
用户关注公众号后把码发给公众号,消息推送 webhook 按 FromUserName(openid)识别用户并批准
对应的登录请求,浏览器带 secret 轮询领取会话。此前的「带参数二维码」只支持已认证服务号,
站长的公众号是未认证个人公众号,不可用。网页授权(snsapi_base)早已移除(需 ICP 备案)。
流程与安全设计见 docs/auth-wechat.md;全部响应 private, no-store。
"""

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from backend.auth import service, wechat_webhook
from backend.auth.config import AuthSettings
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


# ── 发码登录:创建请求 + 4 位验证码 ──────────────────────────

@router.post(
    "/auth/wechat/device",
    response_model=DeviceLoginCreatedDTO,
    responses=AUTH_DISABLED_RESPONSE,
)
def create_device_login(
    request: Request,
    settings: AuthSettings = Depends(get_settings),
    conn=Depends(platform_rw),
):
    """浏览器发起登录:创建一次性 request,返回 4 位验证码(给用户发到公众号)与 secret
    (只留在浏览器内存,领取会话时必须带上;验证码本身不足以领取会话)。"""
    _ensure_wechat_enabled(settings)
    if not limiter.allow(f"device_create:{client_ip_key(request)}", 10, 60):
        raise HTTPException(status_code=429, detail="请求过于频繁")
    try:
        with tx(conn):
            req = service.create_device_request(conn, ttl_seconds=settings.device_request_ttl_seconds)
    except service.LoginCodeExhausted:
        log.error("等待中的登录请求把 4 位验证码几乎占满")
        raise HTTPException(status_code=429, detail="当前登录人数过多,请稍后再试")
    resp = JSONResponse(
        {
            "request_id": req["request_id"],
            "secret": req["secret"],
            "login_code": req["login_code"],
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
    conn=Depends(platform_rw),
):
    """微信服务器推送的消息/事件入口。登录:用户发来 4 位验证码(文本消息)。

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
    if event.msg_type == "event":
        if event.event == "subscribe":
            reply_text = REPLY_WELCOME
        # 取消关注等其它事件:不回复
    else:
        # 用户发来的任何消息(文本/图片/语音…):是验证码就尝试登录,否则提示
        reply_text = _handle_user_message(conn, settings, event)

    if reply_text is not None:
        resp = Response(
            content=wechat_webhook.build_text_reply(event, reply_text, int(time.time())),
            media_type="application/xml",
        )
    else:
        resp = PlainTextResponse("success")
    _no_store(resp)
    return resp


# 公众号被动回复文案(微信聊天里用全角标点;站长定:非验证码消息一律回"验证码错误，请重新输入")
REPLY_WELCOME = "欢迎关注喵弟数据研究室！想登录网站，把网页上的 4 位验证码发给我就行。"
REPLY_NOT_A_CODE = "验证码错误，请重新输入"
REPLY_CODE_INVALID = "验证码无效或已过期，请回到网页重新获取"
REPLY_TOO_MANY = "尝试次数过多，请 10 分钟后再试"
REPLY_SUCCESS = "登录成功，请回到网页继续"
# 同一个微信号 10 分钟内最多发错 10 次验证码(防止乱猜别人的码);只对发错计数,
# 正常登录不消耗次数
CODE_FAILURES_PER_WINDOW = 10
CODE_FAILURE_WINDOW_SECONDS = 600


def _handle_user_message(conn, settings: AuthSettings, event: wechat_webhook.WechatEvent) -> str:
    code = wechat_webhook.extract_login_code(event.content)
    if code is None:
        return REPLY_NOT_A_CODE
    fail_key = f"wx_code_fail:{event.openid}"
    if limiter.blocked(fail_key, CODE_FAILURES_PER_WINDOW, CODE_FAILURE_WINDOW_SECONDS):
        return REPLY_TOO_MANY
    with tx(conn):
        row = service.get_pending_request_by_code(conn, code)
        if row is None:
            limiter.record(fail_key)
            return REPLY_CODE_INVALID
        # 对上了才建/取用户——发错码不会凭空产生账号
        user_id = service.get_or_create_user_by_identity(
            conn,
            provider="wechat_oa",
            provider_app_id=settings.identity_app_id,
            provider_subject=event.openid,
        )
        ok = service.approve_device_request(conn, row["id"], user_id)
    if not ok:
        return REPLY_CODE_INVALID
    log.info("device request %s 已由公众号验证码批准", row["id"])
    return REPLY_SUCCESS


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
        "user": {
            "id": ctx.user_id,
            "display_name": ctx.display_name,
            "role": ctx.role,
            "short_code": ctx.short_code,
            "nickname_set": ctx.nickname_set,
        },
        "plan": ctx.plan_id,
        "entitlements": sorted(ctx.entitlements),
        "session_expires_at": ctx.session_row["expires_at"],
    }
