"""微信认证 Provider Adapter(带参数二维码 + webhook 路线,2026-08 起)。

网页授权(snsapi_base)路线已废弃:网页授权域名要求 ICP 备案,本项目部署 AWS 东京
且不迁回大陆备案(CLAUDE.md §7.3,修改已获用户批准)。登录唯一触发方式是:
公众号「生成带参数的二维码」接口(scene_str = device request id)+ 用户扫码后
微信服务器推送事件到本站 webhook(backend/auth/wechat_webhook.py)。

- RealWechatQrProvider:真实公众号。access_token 每 AppID 全局唯一、重新获取会使
  上一个立即失效 → 缓存持久化在 platform.db(wechat_access_token_cache),跨进程共享,
  只在临过期时串行刷新。真实网络能力未验证时标 UNVERIFIED(docs/auth-wechat.md §9)。
- DisabledWechatProvider:WECHAT_AUTH_ENABLED=0 时的显式占位,公开站点可无凭证启动。
- MockWechatProvider:仅 development;production 在 config 层 fail-fast,
  build_provider 里再拦一次。webhook 入站链路不依赖 Provider,Mock 只伪造 QR 创建。

年审单点(如实):公众号接口权限绑定微信认证年审,年审过期时 qrcode/create 返回
errcode=48001 —— 该错误按结构化 AuthProviderError(errcode=48001) 抛出并记录,
本轮不做降级通道。
"""

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import timedelta

from backend.db.util import utc_now, utc_now_iso

from .config import AuthConfigError, AuthSettings

ACCESS_TOKEN_REFRESH_MARGIN_SECONDS = 300
WECHAT_API_TIMEOUT_SECONDS = 10

# access_token 失效类错误码:清缓存重取一次(不无限重试)
_TOKEN_INVALID_ERRCODES = {40001, 40014, 42001}


class AuthProviderError(RuntimeError):
    def __init__(self, message: str, errcode: int | None = None):
        super().__init__(message)
        self.errcode = errcode


@dataclass(frozen=True)
class LoginQrCode:
    ticket: str
    url: str              # 二维码真实内容(前端本地 canvas 渲染,不依赖微信图片外链)
    expire_seconds: int


def _iso(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class RealWechatQrProvider:
    kind = "real"

    def __init__(self, app_id: str, app_secret: str, transport=None):
        if not app_id or not app_secret:
            raise AuthConfigError("RealWechatQrProvider 需要 AppID/AppSecret")
        self._app_id = app_id
        self._app_secret = app_secret
        # 测试注入口:httpx.MockTransport;None=真实网络
        self._transport = transport

    @property
    def app_id(self) -> str:
        return self._app_id

    # ── HTTP(唯一网络出口;错误详情只留服务端) ────────────

    def _request(self, method: str, url: str, **kwargs) -> dict:
        import httpx

        client_kwargs = {"timeout": WECHAT_API_TIMEOUT_SECONDS}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
        with httpx.Client(**client_kwargs) as client:
            resp = client.request(method, url, **kwargs)
        try:
            return resp.json()
        except json.JSONDecodeError as e:
            raise AuthProviderError(f"微信 API 返回非 JSON(HTTP {resp.status_code})") from e

    # ── access_token(platform.db 缓存,跨进程共享) ───────

    # 刷新串行化:进程内锁。网络请求绝不持有 SQLite 写锁(§5.3 短事务);
    # 跨进程共享靠 DB 缓存行——本栈只有 API 进程会取 token,CLI/Worker 均不取,
    # 因此进程内串行已足以避免"并发重取互相顶掉"。
    _refresh_lock = threading.Lock()

    def get_access_token(self, conn: sqlite3.Connection) -> str:
        """返回有效 access_token;临过期(<300s)才刷新。"""
        margin = _iso(utc_now() + timedelta(seconds=ACCESS_TOKEN_REFRESH_MARGIN_SECONDS))
        row = conn.execute(
            "SELECT access_token, expires_at FROM wechat_access_token_cache WHERE app_id=?",
            (self._app_id,),
        ).fetchone()
        if row is not None and row["expires_at"] > margin:
            return row["access_token"]

        with RealWechatQrProvider._refresh_lock:
            # 拿到锁后重查:可能别的请求刚刷新完
            row = conn.execute(
                "SELECT access_token, expires_at FROM wechat_access_token_cache WHERE app_id=?",
                (self._app_id,),
            ).fetchone()
            if row is not None and row["expires_at"] > margin:
                return row["access_token"]
            fetched_at = utc_now()
            token = self._fetch_access_token()   # 网络调用,不持任何 DB 锁
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO wechat_access_token_cache"
                    " (app_id, access_token, expires_at, fetched_at) VALUES (?, ?, ?, ?)",
                    (self._app_id, token["access_token"],
                     _iso(fetched_at + timedelta(seconds=token["expires_in"])),
                     utc_now_iso()),
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            return token["access_token"]

    def _fetch_access_token(self) -> dict:
        data = self._request(
            "GET",
            "https://api.weixin.qq.com/cgi-bin/token",
            params={
                "grant_type": "client_credential",
                "appid": self._app_id,
                "secret": self._app_secret,
            },
        )
        if "access_token" not in data:
            raise AuthProviderError(
                f"获取 access_token 失败: errcode={data.get('errcode')}",
                errcode=data.get("errcode"),
            )
        return data

    def invalidate_access_token(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "DELETE FROM wechat_access_token_cache WHERE app_id=?", (self._app_id,)
        )

    # ── 带参数二维码 ──────────────────────────────────────

    def create_login_qrcode(
        self, conn: sqlite3.Connection, scene_str: str, expire_seconds: int
    ) -> LoginQrCode:
        """QR_STR_SCENE 临时二维码,scene_str = device request id(公开值)。

        token 失效类 errcode(40001/40014/42001)清缓存重取一次;
        48001(接口权限异常,常见于年审过期)原样抛出,由路由层结构化处理。
        """
        data = self._qrcode_create(conn, scene_str, expire_seconds)
        errcode = data.get("errcode")
        if errcode in _TOKEN_INVALID_ERRCODES:
            self.invalidate_access_token(conn)
            data = self._qrcode_create(conn, scene_str, expire_seconds)
            errcode = data.get("errcode")
        if "ticket" not in data:
            raise AuthProviderError(
                f"创建带参二维码失败: errcode={errcode}", errcode=errcode
            )
        return LoginQrCode(
            ticket=data["ticket"],
            url=data["url"],
            expire_seconds=int(data.get("expire_seconds", expire_seconds)),
        )

    def _qrcode_create(self, conn, scene_str: str, expire_seconds: int) -> dict:
        access_token = self.get_access_token(conn)
        return self._request(
            "POST",
            "https://api.weixin.qq.com/cgi-bin/qrcode/create",
            params={"access_token": access_token},
            json={
                "expire_seconds": expire_seconds,
                "action_name": "QR_STR_SCENE",
                "action_info": {"scene": {"scene_str": scene_str}},
            },
        )


class DisabledWechatProvider:
    """WECHAT_AUTH_ENABLED=0(real 未开启)时的显式占位 Provider(CLAUDE.md §7.3 三态)。

    公开站点必须可以无微信凭证启动:不实例化 Real Provider,也不校验凭证。
    微信端点在路由层(settings.wechat_login_available)直接返回 503 AUTH_DISABLED,
    正常情况下不会调用到这里;万一被调用,抛错兜底而不是静默放行。
    """

    kind = "disabled"
    app_id = ""

    def create_login_qrcode(self, conn, scene_str: str, expire_seconds: int) -> LoginQrCode:
        raise AuthProviderError("微信登录未启用(AUTH_DISABLED),不应调用 create_login_qrcode")


class MockWechatProvider:
    """development 专用:不发任何网络请求,伪造 QR 创建结果。

    webhook 入站链路(签名校验/XML 解析/批准)与 Provider 无关,Mock 环境下
    对 webhook 端点 POST 一条按 dev Token 签名的 SCAN 事件即可模拟扫码
    (backend/cli/simulate_wechat_scan.py)。
    """

    kind = "mock"
    app_id = "mock-app"

    def create_login_qrcode(self, conn, scene_str: str, expire_seconds: int) -> LoginQrCode:
        return LoginQrCode(
            ticket=f"mock-ticket-{scene_str}",
            url=f"https://example.invalid/mock-wechat-qr/{scene_str}",
            expire_seconds=expire_seconds,
        )


def build_provider(settings: AuthSettings):
    if settings.wechat_provider_kind == "real":
        if not settings.wechat_auth_enabled:
            # 三态之一:production/development + real + ENABLED=0
            # → 无凭证可启动,不得尝试实例化真实 Provider(CLAUDE.md §7.3)
            return DisabledWechatProvider()
        return RealWechatQrProvider(settings.wechat_app_id, settings.wechat_app_secret)
    if settings.is_production:
        raise AuthConfigError("production 禁止实例化 MockWechatProvider(fail-fast)")
    return MockWechatProvider()
