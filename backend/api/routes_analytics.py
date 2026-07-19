"""first-party 最小化埋点(P0.10)。不存 OpenID、完整 IP、token。"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend.db.connections import tx
from backend.db.util import utc_now_iso

from .deps import NO_STORE, AuthContext, client_ip_key, get_auth_context, platform_rw
from .ratelimit import limiter

router = APIRouter(prefix="/api/v1", tags=["analytics"])

ALLOWED_EVENTS = {
    "landing_view",
    "match_view",
    "login_started",
    "login_completed",
    "premium_preview_viewed",
    "pricing_viewed",
    "redeem_completed",
    "favorite_added",
    "studio_exported",
}


class AnalyticsEventBody(BaseModel):
    event: str
    anon_id: str | None = Field(None, max_length=64)     # 前端随机 id,非 OpenID
    path: str | None = Field(None, max_length=300)
    referrer: str | None = Field(None, max_length=300)
    meta: dict = Field(default_factory=dict)


@router.post("/analytics/events", status_code=204)
def record_event(
    body: AnalyticsEventBody,
    request: Request,
    response: Response,
    ctx: AuthContext = Depends(get_auth_context),
    conn=Depends(platform_rw),
):
    response.headers["Cache-Control"] = NO_STORE
    if body.event not in ALLOWED_EVENTS:
        raise HTTPException(status_code=400, detail="未知事件")
    # 限流键仅内存使用,不落库(不保存 IP)
    if not limiter.allow(f"analytics:{client_ip_key(request)}", 60, 60):
        return
    import json

    meta = json.dumps(body.meta, ensure_ascii=False)
    if len(meta) > 2000:
        raise HTTPException(status_code=400, detail="meta 过大")
    with tx(conn):
        conn.execute(
            "INSERT INTO analytics_events (event, anon_id, user_id, path, referrer, meta_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (body.event, body.anon_id, ctx.user_id, body.path, body.referrer, meta, utc_now_iso()),
        )
