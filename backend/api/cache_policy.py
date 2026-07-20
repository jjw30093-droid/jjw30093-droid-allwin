"""集中式响应缓存策略(app 层 default-deny;CLAUDE.md §10.2 的运行时落地)。

背景(真实探测已确认,不是假设性加固):
- FastAPI 在 endpoint 内 raise 异常时,注入的 `Response` 对象上设置的 header 会被丢弃,
  只有异常处理器产出的 JSONResponse 才到达客户端——各 endpoint 里 `response.headers[...]`
  这类写法从未覆盖过自己的异常路径(401/403/404/422/500 等一律没有 Cache-Control)。
- `routes_public.py` 的"公开"端点(products/track-record/matches 等)在设置
  `public, s-maxage=...` 时完全没有检查请求是否带 Cookie/Authorization。
- `backend/api_server.py` 的 4 个 legacy 端点从不设置任何 Cache-Control(成功或失败路径都没有)。

与其在几十个 endpoint 里逐个补丁(且异常路径这类 gap 天然无法用"每个 endpoint 自己设置"
的方式覆盖),这里用一个纯 ASGI 中间件在响应真正发出前统一兜底,规则(优先级从高到低):

1. 请求带 Cookie 或 Authorization → 强制 `private, no-store`,不论 endpoint 声明了什么;
2. 响应带 Set-Cookie → 强制 `private, no-store`;
3. 路径不在 PUBLIC_ALLOWLIST → 强制 `private, no-store`(即便 endpoint 误标 public,
   例如未来新增/误改的端点忘记检查身份就写了 public);
4. 以上都不触发,且 endpoint 已显式声明 Cache-Control → 保留原样(信任 endpoint 自己
   按 league_id/entitlement 做出的 public/private 选择,中间件不会把它从 private
   "升级"成 public,只在 (1)(2)(3) 之外做 no-op);
5. 以上都不触发,但 endpoint 完全没有设置 Cache-Control(异常处理器、healthz/readyz、
   legacy app 的遗漏)→ 默认 `private, no-store`。

PUBLIC_ALLOWLIST 只是"这条路径允许出现 public 缓存"的上限,不代表这些路径总是 public——
实际 public/private 取舍仍由 endpoint 自身按 league_id==47、匿名/登录等条件决定
(league_fixtures/matches 等对非英超联赛已经自己设置 NO_STORE,这里原样保留)。

用纯 ASGI 中间件(而不是 BaseHTTPMiddleware)只改写 `http.response.start` 消息的
header 列表,不触碰任何 `http.response.body` 消息——StreamingResponse/FileResponse
的分块发送不受影响,不会被缓冲或改变。
"""

from starlette.datastructures import MutableHeaders

NO_STORE = "private, no-store"

# (method, 完整路径模板) —— APIRoute.path 已经是包含 router prefix 的完整路径
# (例如 "/api/v1/leagues/{league_id}/fixtures"),不需要再拼前缀。
PUBLIC_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/v1/products"),
        ("GET", "/api/v1/track-record"),
        ("GET", "/api/v1/model/metrics"),
        ("GET", "/api/v1/leagues/{league_id}/standings"),
        ("GET", "/api/v1/leagues/{league_id}/fixtures"),
        ("GET", "/api/v1/matches"),
        ("GET", "/api/v1/matches/{match_id}"),
    }
)


def _request_has_credentials(raw_headers: list[tuple[bytes, bytes]]) -> bool:
    for key, _value in raw_headers:
        if key in (b"cookie", b"authorization"):
            return True
    return False


def _route_key(scope) -> tuple[str, str] | None:
    method = scope.get("method")
    route = scope.get("route")
    path = getattr(route, "path", None)
    if method is None or path is None:
        return None
    return (method, path)


def _append_vary(headers: MutableHeaders, token: str) -> None:
    existing = [p.strip() for p in (headers.get("vary") or "").split(",") if p.strip()]
    if token not in existing:
        existing.append(token)
    headers["vary"] = ", ".join(existing)


class CachePolicyMiddleware:
    """纯 ASGI 中间件:只在 `http.response.start` 上做 header 决策,不缓冲 body。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        has_credentials = _request_has_credentials(scope.get("headers", []))

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                has_set_cookie = "set-cookie" in headers
                if has_credentials or has_set_cookie:
                    headers["cache-control"] = NO_STORE
                    if has_credentials:
                        _append_vary(headers, "Cookie")
                elif _route_key(scope) not in PUBLIC_ALLOWLIST:
                    headers["cache-control"] = NO_STORE
                elif headers.get("cache-control") is None:
                    headers["cache-control"] = NO_STORE
                # 否则:allowlist 内 + 无凭证 + 无 Set-Cookie + endpoint 已显式声明
                # → 保留原样,不做任何改写。
            await send(message)

        await self.app(scope, receive, send_wrapper)


def install_cache_policy(app) -> None:
    app.add_middleware(CachePolicyMiddleware)
