"""登录用户端点:兑换码、收藏、账户。全部 private, no-store。"""

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from backend.commands.redeem import RedeemError, redeem_code
from backend.db.connections import tx
from backend.db.util import utc_now_iso

from .deps import NO_STORE, AuthContext, platform_ro, platform_rw, require_csrf, require_user
from .schemas import (
    AccountResponse,
    FavoritesResponse,
    OkDTO,
    RedeemResponse,
    error_responses,
)

router = APIRouter(
    prefix="/api/v1",
    tags=["member"],
    responses=error_responses(400, 401, 403, 404, 422),
)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = NO_STORE


class RedeemBody(BaseModel):
    code: str = Field(min_length=6, max_length=32)


@router.post("/redeem", response_model=RedeemResponse)
def redeem(
    body: RedeemBody,
    response: Response,
    ctx: AuthContext = Depends(require_csrf),
    conn=Depends(platform_rw),
):
    _no_store(response)
    try:
        with tx(conn):
            grant = redeem_code(conn, body.code, ctx.user_id)
    except RedeemError as e:
        msg = {"invalid": "兑换码无效", "expired": "兑换码已过期", "used": "兑换码已被使用"}[e.reason]
        raise HTTPException(status_code=400, detail={"code": e.reason, "message": msg})
    return {"status": "ok", **grant}


class FavoriteBody(BaseModel):
    match_id: int


@router.post("/favorites", response_model=OkDTO)
def add_favorite(
    body: FavoriteBody,
    response: Response,
    ctx: AuthContext = Depends(require_csrf),
    conn=Depends(platform_rw),
):
    _no_store(response)
    with tx(conn):
        conn.execute(
            "INSERT OR IGNORE INTO favorites (user_id, match_id, created_at) VALUES (?, ?, ?)",
            (ctx.user_id, body.match_id, utc_now_iso()),
        )
    return {"status": "ok"}


@router.delete("/favorites/{match_id}", response_model=OkDTO)
def remove_favorite(
    match_id: int,
    response: Response,
    ctx: AuthContext = Depends(require_csrf),
    conn=Depends(platform_rw),
):
    _no_store(response)
    with tx(conn):
        conn.execute(
            "DELETE FROM favorites WHERE user_id=? AND match_id=?", (ctx.user_id, match_id)
        )
    return {"status": "ok"}


@router.get("/favorites", response_model=FavoritesResponse)
def list_favorites(
    response: Response,
    ctx: AuthContext = Depends(require_user),
    conn=Depends(platform_ro),
):
    _no_store(response)
    rows = conn.execute(
        "SELECT match_id, created_at FROM favorites WHERE user_id=? ORDER BY created_at DESC",
        (ctx.user_id,),
    ).fetchall()
    return {"favorites": [dict(r) for r in rows]}


@router.get("/account", response_model=AccountResponse)
def account(
    response: Response,
    ctx: AuthContext = Depends(require_user),
    conn=Depends(platform_ro),
):
    _no_store(response)
    identities = conn.execute(
        "SELECT provider, provider_app_id, created_at, last_used_at"
        " FROM auth_identities WHERE user_id=?",
        (ctx.user_id,),
    ).fetchall()
    subs = conn.execute(
        "SELECT id, plan_id, status, starts_at, ends_at, source, created_at"
        " FROM subscriptions WHERE user_id=? ORDER BY ends_at DESC LIMIT 20",
        (ctx.user_id,),
    ).fetchall()
    sessions = conn.execute(
        "SELECT id, created_at, last_seen_at, expires_at, user_agent,"
        " CASE WHEN id=? THEN 1 ELSE 0 END AS is_current"
        " FROM auth_sessions WHERE user_id=? AND revoked_at IS NULL AND expires_at > ?"
        " ORDER BY created_at DESC",
        (ctx.session_id, ctx.user_id, utc_now_iso()),
    ).fetchall()
    return {
        "user": {"id": ctx.user_id, "display_name": ctx.display_name, "role": ctx.role},
        "plan": ctx.plan_id,
        "entitlements": sorted(ctx.entitlements),
        "identities": [dict(r) for r in identities],
        "subscriptions": [dict(r) for r in subs],
        "sessions": [dict(r) for r in sessions],
        # MVP 未接短信/邮件,账号恢复能力如实说明(CLAUDE.md §7.4)
        "recovery": {"available": False, "note": "当前仅微信登录,尚未支持绑定备用恢复方式"},
    }


class RevokeSessionBody(BaseModel):
    session_id: str


@router.post("/account/sessions/revoke", response_model=OkDTO)
def revoke_other_session(
    body: RevokeSessionBody,
    response: Response,
    ctx: AuthContext = Depends(require_csrf),
    conn=Depends(platform_rw),
):
    _no_store(response)
    with tx(conn):
        cur = conn.execute(
            "UPDATE auth_sessions SET revoked_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL",
            (utc_now_iso(), body.session_id, ctx.user_id),
        )
    if cur.rowcount != 1:
        raise HTTPException(status_code=404, detail="会话不存在或已撤销")
    return {"status": "ok"}
