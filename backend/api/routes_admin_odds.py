"""/api/v1/admin/xref:NowGoal ↔ FotMob 比赛映射人工审核(P0.7)。

- GET  ""                 pending 列表(含 needs_review),odds.db 只读;
- POST "/{xref_id}/confirm" 确认映射(verified=1 只能在这里由人置位);
- POST "/{xref_id}/reject"  驳回映射。
写操作全部过 require_admin_csrf,并写 platform.db audit_logs 留痕;响应一律 no-store。
"""

from fastapi import APIRouter, Depends, HTTPException, Response

from backend.commands.audit import write_audit
from backend.db.connections import connect_rw, tx
from backend.db.util import utc_now_iso

from .deps import NO_STORE, AuthContext, odds_ro, platform_rw, require_admin
from .routes_admin import require_admin_csrf
from .schemas import XrefListResponse, XrefReviewResultDTO, error_responses

router = APIRouter(
    prefix="/api/v1/admin/xref",
    tags=["admin-xref"],
    responses=error_responses(401, 403, 404, 422),
)


def odds_rw():
    """odds.db 写连接(仅本审核模块需要,deps.py 只提供 odds_ro)。"""
    conn = connect_rw("odds")
    try:
        yield conn
    finally:
        conn.close()


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = NO_STORE


@router.get("", response_model=XrefListResponse)
def list_xrefs(
    response: Response,
    status: str = "",
    limit: int = 100,
    offset: int = 0,
    ctx: AuthContext = Depends(require_admin),
    conn=Depends(odds_ro),
):
    """映射列表。默认返回待处理(needs_review + auto_ok);?status= 精确过滤。"""
    _no_store(response)
    limit = max(1, min(limit, 500))
    if status:
        where, params = "review_status = ?", [status]
    else:
        where, params = "review_status IN ('needs_review', 'auto_ok')", []
    rows = conn.execute(
        f"""SELECT id, fotmob_match_id, provider, provider_match_id, home_away_inverted,
                   confidence, verified, method, kickoff_diff_seconds, review_status,
                   created_at, updated_at
            FROM dim_match_xref WHERE {where}
            ORDER BY review_status DESC, confidence ASC, id LIMIT ? OFFSET ?""",
        (*params, limit, offset),
    ).fetchall()
    counts = conn.execute(
        "SELECT review_status, COUNT(*) AS n FROM dim_match_xref GROUP BY review_status"
    ).fetchall()
    return {
        "counts": {r["review_status"]: r["n"] for r in counts},
        "xrefs": [dict(r) for r in rows],
    }


def _review_xref(
    xref_id: int,
    new_status: str,
    verified: int,
    action: str,
    ctx: AuthContext,
    conn_odds,
    conn_platform,
) -> dict:
    row = conn_odds.execute(
        "SELECT * FROM dim_match_xref WHERE id = ?", (xref_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="映射不存在")
    with tx(conn_odds):
        conn_odds.execute(
            "UPDATE dim_match_xref SET review_status=?, verified=?, method='manual', updated_at=?"
            " WHERE id=?",
            (new_status, verified, utc_now_iso(), xref_id),
        )
    with tx(conn_platform):
        write_audit(
            conn_platform,
            action=action,
            actor_user_id=ctx.user_id,
            target_type="dim_match_xref",
            target_id=xref_id,
            detail={
                "provider": row["provider"],
                "provider_match_id": row["provider_match_id"],
                "fotmob_match_id": row["fotmob_match_id"],
                "prev_review_status": row["review_status"],
                "home_away_inverted": row["home_away_inverted"],
                "confidence": row["confidence"],
            },
        )
    return {"status": "ok", "xref_id": xref_id, "review_status": new_status}


@router.post("/{xref_id}/confirm", response_model=XrefReviewResultDTO)
def confirm_xref(
    xref_id: int,
    response: Response,
    ctx: AuthContext = Depends(require_admin_csrf),
    conn_odds=Depends(odds_rw),
    conn_platform=Depends(platform_rw),
):
    _no_store(response)
    return _review_xref(xref_id, "confirmed", 1, "xref.confirm", ctx, conn_odds, conn_platform)


@router.post("/{xref_id}/reject", response_model=XrefReviewResultDTO)
def reject_xref(
    xref_id: int,
    response: Response,
    ctx: AuthContext = Depends(require_admin_csrf),
    conn_odds=Depends(odds_rw),
    conn_platform=Depends(platform_rw),
):
    _no_store(response)
    return _review_xref(xref_id, "rejected", 0, "xref.reject", ctx, conn_odds, conn_platform)
