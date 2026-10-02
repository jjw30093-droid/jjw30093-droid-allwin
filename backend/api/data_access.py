"""比赛/联赛数据的登录门禁与防爬限流(2026-10-02,经站长批准)。

站长口径:
1. 未登录只能看英超(`ANON_LEAGUE_IDS`)的比赛详情与联赛数据;其他联赛必须
   登录。比赛列表里的对阵/时间/胜平负概率对任何人可见,点进去才要求登录。
2. 公推与战绩照常公开(不经过本模块)。
3. 防爬:限制短时间内快速刷数据,正常浏览不受影响;触发限流的账号在后台
   留痕(audit_logs `ratelimit.trip`),由管理员决定是否停用账号。

限流只是"提高成本 + 留证据",不是绝对防线——模拟正常手速的爬虫拦不住,
这一点站长已知悉。
"""

from __future__ import annotations

import logging
import threading
import time

from fastapi import Depends, HTTPException, Request

from backend.commands.audit import write_audit
from backend.db.connections import connect_rw, tx
from backend.queries.leagues import LEAGUE_META, is_anonymous_league

from .deps import AuthContext, get_auth_context
from .ratelimit import limiter

log = logging.getLogger("allwin.api.data_access")

LOGIN_REQUIRED = "login_required"
RATE_LIMITED = "rate_limited"

# 站长 2026-10-02 口径:"正常人浏览频率 × 3"。正常人浏览频率取生产 nginx 访问
# 日志近 14 天真人访客(排除爬虫 UA,2921 个 IP+UA)打开比赛详情/联赛页的 p95:
# 每分钟 4 页、每小时 5 页、每天 15 页 → ×3 = 12 / 15 / 45 页。
# 额度按请求数计:非英超比赛详情页打开一次实测 6 个数据请求(最贵的一种页面),
# 所以 1 页 = PAGE_COST 次。页面没有定时轮询数据端点。
PAGE_COST = 6
USER_PER_MIN = 12 * PAGE_COST     # 72
USER_PER_HOUR = 15 * PAGE_COST    # 90
USER_PER_DAY = 45 * PAGE_COST     # 270
# 比赛列表对任何人一致且公开(未登录也能看),不计入登录用户的额度
UNMETERED_PATHS = frozenset({"/api/v1/matches"})
# 未登录只能拿到英超(本来就公开),这档只防狂刷;运营商 NAT 下多人共用
# 一个出口 IP,所以给得宽。
ANON_IP_PER_MIN = 300
# 同一账号 10 分钟内只记一条留痕,避免狂刷时把审计表刷爆。
TRIP_AUDIT_COOLDOWN_SECONDS = 600

_trip_lock = threading.Lock()
_last_trip_audit: dict[str, float] = {}


def _record_user_trip(user_id: str, window: str) -> None:
    now = time.monotonic()
    with _trip_lock:
        last = _last_trip_audit.get(user_id)
        if last is not None and now - last < TRIP_AUDIT_COOLDOWN_SECONDS:
            return
        _last_trip_audit[user_id] = now
    try:
        conn = connect_rw("platform")
        try:
            with tx(conn):
                write_audit(
                    conn,
                    action="ratelimit.trip",
                    actor_user_id=user_id,
                    actor_type="system",
                    target_type="user",
                    target_id=user_id,
                    detail={"window": window},
                )
        finally:
            conn.close()
    except Exception:  # 留痕失败不影响限流本身
        log.exception("ratelimit trip audit failed")


def data_access_ctx(
    request: Request, ctx: AuthContext = Depends(get_auth_context)
) -> AuthContext:
    """数据端点公共依赖:识别身份 + 防爬限流。门禁判定见 require_league_access。"""
    if ctx.authenticated:
        if request.url.path in UNMETERED_PATHS:
            return ctx
        key = f"data_user:{ctx.user_id}"
        if not limiter.allow(f"{key}:m", USER_PER_MIN, 60):
            _record_user_trip(ctx.user_id, "minute")
            raise HTTPException(status_code=429, detail={"code": RATE_LIMITED, "message": "访问过于频繁,请稍后再试"})
        if not limiter.allow(f"{key}:h", USER_PER_HOUR, 3600):
            _record_user_trip(ctx.user_id, "hour")
            raise HTTPException(status_code=429, detail={"code": RATE_LIMITED, "message": "访问过于频繁,请稍后再试"})
        if not limiter.allow(f"{key}:d", USER_PER_DAY, 86400):
            _record_user_trip(ctx.user_id, "day")
            raise HTTPException(status_code=429, detail={"code": RATE_LIMITED, "message": "今天的访问次数已用完,请明天再来"})
        return ctx
    ip = request.headers.get("x-real-ip")
    # 没有 X-Real-IP = 没经过 nginx = Next.js 服务端渲染直连 127.0.0.1
    # (FastAPI 只监听本机,外部无法绕过 nginx)。SSR 不带用户 Cookie,
    # 只能拿到英超,不计入限流,否则全站 SSR 共用一个桶会互相挤掉。
    if ip and not limiter.allow(f"data_ip:{ip}", ANON_IP_PER_MIN, 60):
        log.warning("anonymous data rate limit tripped")
        raise HTTPException(status_code=429, detail={"code": RATE_LIMITED, "message": "访问过于频繁,请稍后再试"})
    return ctx


def require_league_access(ctx: AuthContext, league_id: int, match: dict | None = None) -> None:
    """未知联赛 404;未登录访问非英超 401(login_required)。

    401 的 details 只带列表页本来就公开的赛事标识(联赛名、对阵、开球时间),
    让登录引导能说清"哪场比赛",不含任何详情数据。
    """
    meta = LEAGUE_META.get(league_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="未知联赛")
    if ctx.authenticated or is_anonymous_league(league_id):
        return
    detail: dict = {
        "code": LOGIN_REQUIRED,
        "message": "登录后查看",
        "league_id": league_id,
        "league_name": meta["name_zh"],
    }
    if match is not None:
        detail["home_team"] = match["home"]["name"]
        detail["away_team"] = match["away"]["name"]
        detail["kickoff_at_utc"] = match.get("kickoff_at_utc")
    raise HTTPException(status_code=401, detail=detail)
