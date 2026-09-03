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
# 公开且身份无关的响应可用的两档共享缓存(供各 route 模块 import,不再各自
# 重复定义;原定义在 routes_public.py,2026-09 每日公推新增端点需要同一
# 常量,顺势收敛到缓存策略的单一真源里,字符串值一字节不变)。
PUBLIC_CACHE = "public, s-maxage=300, stale-while-revalidate=60"
PUBLIC_CACHE_SHORT = "public, s-maxage=60, stale-while-revalidate=30"

# (method, 完整路径模板) —— APIRoute.path 已经是包含 router prefix 的完整路径
# (例如 "/api/v1/leagues/{league_id}/fixtures"),不需要再拼前缀。
PUBLIC_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/v1/products"),
        ("GET", "/api/v1/track-record"),
        # 只是三个聚合时间戳,不含比赛/推荐内容,身份无关,允许共享缓存。
        ("GET", "/api/v1/status/freshness"),
        ("GET", "/api/v1/model/metrics"),
        ("GET", "/api/v1/leagues/{league_id}/standings"),
        ("GET", "/api/v1/leagues/{league_id}/fixtures"),
        ("GET", "/api/v1/leagues/{league_id}/team-stats"),
        ("GET", "/api/v1/leagues/{league_id}/players"),
        # 联赛赛季速览:纯赛季聚合(进球时段/比分分布/大小球阈值/主平客),
        # 响应不随身份变化。免费联赛给 s-maxage;需登录联赛在 endpoint 内已判为
        # NO_STORE,带 Cookie 的请求另由规则 1 强制 no-store。
        ("GET", "/api/v1/leagues/{league_id}/season-profile"),
        ("GET", "/api/v1/matches"),
        ("GET", "/api/v1/matches/{match_id}"),
        # 完赛事实报告:纯历史事实(阵容/事件/射门/统计),响应不随身份变化;
        # 带 Cookie 的请求仍会被下方规则 1 强制 no-store,不会污染共享缓存。
        ("GET", "/api/v1/matches/{match_id}/report"),
        # 赛前市场卡:两队历史聚合 + 离线标定表查表,不区分付费档位,
        # 响应同样不随身份变化。
        ("GET", "/api/v1/matches/{match_id}/markets"),
        # 赛前预览(阵容/伤停快照 + 风格象限 + 进攻来源 + 关键球员 + 门将):
        # 与 /report、/markets 同级,全部历史聚合与已采集快照,不区分付费档位。
        ("GET", "/api/v1/matches/{match_id}/preview"),
        ("GET", "/api/v1/media/team-crests/{provider}/{provider_team_id}.png"),
        # 每日公推(board='daily_public',2026-09 新增):完全公开、匿名可见,
        # 响应不随身份变化——与 /api/v1/products 同一性质。带 Cookie 的请求
        # 仍由上方规则 1 强制 no-store,不会污染共享缓存。注意:
        # /api/v1/reco/daily(/{slip_id})、/reco/my-access、/reco/overview
        # 与全部 /admin/reco/* 仍然**不在**本 allowlist 内,继续走 default-deny。
        ("GET", "/api/v1/reco/public"),
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
