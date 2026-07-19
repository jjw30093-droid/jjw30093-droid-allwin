"""微信认证 Provider Adapter。

- RealWechatOAProvider:已认证服务号网页授权(snsapi_base)。真实凭证缺失时无法验证,
  外部能力状态见 docs/auth-wechat.md(UNVERIFIED 标记)。
- MockWechatProvider:仅 development。production 在 config 层 fail-fast,双保险在
  build_provider 里再拦一次。
"""

from dataclasses import dataclass
from urllib.parse import quote

from .config import AuthConfigError, AuthSettings


class AuthProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class WechatIdentity:
    openid: str
    union_id: str | None = None   # 仅微信真实返回时才有值,不得推测


class RealWechatOAProvider:
    kind = "real"

    def __init__(self, app_id: str, app_secret: str):
        if not app_id or not app_secret:
            raise AuthConfigError("RealWechatOAProvider 需要 AppID/AppSecret")
        self._app_id = app_id
        self._app_secret = app_secret

    @property
    def app_id(self) -> str:
        return self._app_id

    def authorize_url(self, redirect_uri: str, state: str) -> str:
        return (
            "https://open.weixin.qq.com/connect/oauth2/authorize"
            f"?appid={self._app_id}"
            f"&redirect_uri={quote(redirect_uri, safe='')}"
            "&response_type=code"
            "&scope=snsapi_base"
            f"&state={state}"
            "#wechat_redirect"
        )

    def exchange_code(self, code: str) -> WechatIdentity:
        import httpx

        resp = httpx.get(
            "https://api.weixin.qq.com/sns/oauth2/access_token",
            params={
                "appid": self._app_id,
                "secret": self._app_secret,
                "code": code,
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
        data = resp.json()
        if "openid" not in data:
            # 不把微信错误详情(可能含敏感信息)透传给客户端,只留服务端摘要
            raise AuthProviderError(f"wechat oauth failed: errcode={data.get('errcode')}")
        return WechatIdentity(openid=data["openid"], union_id=data.get("unionid"))


class MockWechatProvider:
    """development 专用:authorize_url 直接 302 回本站 callback,code=mock-<subject>。"""

    kind = "mock"
    app_id = "mock-app"

    def authorize_url(self, redirect_uri: str, state: str) -> str:
        sep = "&" if "?" in redirect_uri else "?"
        return f"{redirect_uri}{sep}code=mock-user-1&state={quote(state, safe='')}"

    def exchange_code(self, code: str) -> WechatIdentity:
        if not code.startswith("mock-"):
            raise AuthProviderError("mock provider 只接受 mock- 前缀 code")
        return WechatIdentity(openid=f"mock-openid-{code[len('mock-'):]}")


def build_provider(settings: AuthSettings):
    if settings.wechat_provider_kind == "real":
        return RealWechatOAProvider(settings.wechat_app_id, settings.wechat_app_secret)
    if settings.is_production:
        raise AuthConfigError("production 禁止实例化 MockWechatProvider(fail-fast)")
    return MockWechatProvider()
