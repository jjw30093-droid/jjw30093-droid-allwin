"""Auth 配置:从环境变量读取。production 缺配置或检测到 Mock 直接拒绝启动(CLAUDE.md §7.3)。"""

import os
from dataclasses import dataclass


class AuthConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthSettings:
    app_env: str                      # development / production
    wechat_auth_enabled: bool
    wechat_provider_kind: str         # real / mock
    wechat_app_id: str
    wechat_app_secret: str
    public_base_url: str              # 回调与二维码的对外地址
    frontend_base_url: str            # 登录后跳转前缀;生产同域留空(相对路径)
    allowed_origins: tuple
    session_ttl_days: int
    oauth_state_ttl_seconds: int
    device_request_ttl_seconds: int
    cookie_secure: bool
    cookie_name: str = "allwin_session"
    csrf_cookie_name: str = "allwin_csrf"
    cookie_path: str = "/api/v1"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


def load_auth_settings(env=None) -> AuthSettings:
    e = env if env is not None else os.environ
    app_env = e.get("APP_ENV", "development")
    enabled = e.get("WECHAT_AUTH_ENABLED", "0") == "1"
    provider_kind = e.get("WECHAT_AUTH_PROVIDER") or ("real" if app_env == "production" else "mock")
    app_id = e.get("WECHAT_OA_APP_ID", "")
    app_secret = e.get("WECHAT_OA_APP_SECRET", "")
    public_base_url = e.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    allowed_origins = tuple(
        o.strip()
        for o in e.get("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
        if o.strip()
    )

    if app_env == "production":
        if provider_kind != "real":
            raise AuthConfigError(
                "production 检测到非 real 的认证 Provider(WECHAT_AUTH_PROVIDER="
                f"{provider_kind!r})——Mock 只允许 development,拒绝启动。"
            )
        if enabled:
            missing = [
                k
                for k, v in {
                    "WECHAT_OA_APP_ID": app_id,
                    "WECHAT_OA_APP_SECRET": app_secret,
                }.items()
                if not v
            ]
            if missing:
                raise AuthConfigError(
                    f"WECHAT_AUTH_ENABLED=1 但 production 缺少 {'/'.join(missing)},拒绝启动。"
                )
            if not public_base_url.startswith("https://"):
                raise AuthConfigError(
                    "production 的 PUBLIC_BASE_URL 必须是 https:// 地址,拒绝启动。"
                )

    return AuthSettings(
        app_env=app_env,
        wechat_auth_enabled=enabled,
        wechat_provider_kind=provider_kind,
        wechat_app_id=app_id,
        wechat_app_secret=app_secret,
        public_base_url=public_base_url.rstrip("/"),
        frontend_base_url=e.get("FRONTEND_BASE_URL", "").rstrip("/"),
        allowed_origins=allowed_origins,
        session_ttl_days=int(e.get("SESSION_TTL_DAYS", "30")),
        oauth_state_ttl_seconds=int(e.get("OAUTH_STATE_TTL_SECONDS", "600")),
        device_request_ttl_seconds=int(e.get("DEVICE_REQUEST_TTL_SECONDS", "300")),
        cookie_secure=(app_env == "production") or e.get("COOKIE_SECURE") == "1",
    )
