"""/api/v1/admin/*:用户/订阅/审计日志/预测登记簿/任务与来源健康
(写操作全部过 CSRF + AuditLog;任务与来源健康只读)。
"""

from fastapi import APIRouter, Depends, HTTPException, Response
from typing import Literal

from pydantic import BaseModel, Field

from backend.auth.service import revoke_all_sessions
from backend.commands.audit import write_audit

from backend.cli.ops_check import _sanitize_summary
from backend.commands.subscriptions import grant_subscription, revoke_subscription
from backend.db.connections import tx
from backend.db.util import utc_now_iso

from .deps import (
    NO_STORE,
    AuthContext,
    odds_ro,
    platform_ro,
    platform_rw,
    require_admin,
    require_csrf,
)
from .schemas import (
    AdminJobsResponse,
    AdminSourceHealthResponse,
    AdminUsersResponse,
    AuditLogsResponse,
    GrantResultDTO,
    OkDTO,
    error_responses,
)

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin"],
    responses=error_responses(400, 401, 403, 404, 409, 422),
)


def require_admin_csrf(ctx: AuthContext = Depends(require_csrf)) -> AuthContext:
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return ctx


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = NO_STORE


@router.get("/users", response_model=AdminUsersResponse)
def list_users(
    response: Response,
    query: str = "",
    limit: int = 50,
    offset: int = 0,
    ctx: AuthContext = Depends(require_admin),
    conn=Depends(platform_ro),
):
    _no_store(response)
    limit = max(1, min(limit, 200))
    now = utc_now_iso()
    week_ago = _days_ago_iso(7)
    like = f"%{query}%"
    rows = conn.execute(
        """SELECT u.id, u.display_name, u.short_code, u.role, u.status, u.created_at, u.last_login_at,
                  (SELECT COUNT(*) FROM audit_logs a
                   WHERE a.actor_user_id=u.id AND a.action='ratelimit.trip'
                     AND a.created_at>=?) AS rate_limit_trips_7d,
                  (SELECT MAX(a.created_at) FROM audit_logs a
                   WHERE a.actor_user_id=u.id AND a.action='ratelimit.trip') AS last_rate_limited_at,
                  (SELECT s.plan_id FROM subscriptions s JOIN plans p ON p.id=s.plan_id
                   WHERE s.user_id=u.id AND s.status='active' AND s.starts_at<=? AND s.ends_at>?
                   ORDER BY p.rank DESC, s.ends_at DESC LIMIT 1) AS plan_id,
                  (SELECT MAX(s.ends_at) FROM subscriptions s
                   WHERE s.user_id=u.id AND s.status='active' AND s.ends_at>?) AS plan_ends_at
           FROM users u
           WHERE (? = '' OR u.display_name LIKE ? OR u.id LIKE ? OR u.short_code LIKE ?)
           ORDER BY u.created_at DESC LIMIT ? OFFSET ?""",
        (week_ago, now, now, now, query, like, like, f"%{query.strip()}%", limit, offset),
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    return {
        "total": total,
        "users": [dict(r) | {"plan_id": r["plan_id"] or "free"} for r in rows],
    }


def _days_ago_iso(days: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


class UserStatusBody(BaseModel):
    status: Literal["active", "disabled"]
    note: str = Field("", max_length=200)


@router.post("/users/{user_id}/status", response_model=OkDTO)
def set_user_status(
    user_id: str,
    body: UserStatusBody,
    response: Response,
    ctx: AuthContext = Depends(require_admin_csrf),
    conn=Depends(platform_rw),
):
    """停用/恢复账号(2026-10-02 防爬:站长对异常刷数据的账号手动封禁)。

    停用即撤销该用户全部会话(立即掉线);停用状态下登录拿到的会话也会被
    get_session_by_token 拒绝。不允许停用自己或其他管理员,防止误操作锁死后台。
    """
    _no_store(response)
    with tx(conn):
        row = conn.execute("SELECT id, role, status FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        if body.status == "disabled" and (user_id == ctx.user_id or row["role"] == "admin"):
            raise HTTPException(status_code=400, detail="不能停用管理员账号")
        conn.execute(
            "UPDATE users SET status=?, updated_at=? WHERE id=?",
            (body.status, utc_now_iso(), user_id),
        )
        revoked = revoke_all_sessions(conn, user_id) if body.status == "disabled" else 0
        write_audit(
            conn,
            action="user.disable" if body.status == "disabled" else "user.enable",
            actor_user_id=ctx.user_id,
            target_type="user",
            target_id=user_id,
            detail={"previous": row["status"], "note": body.note, "sessions_revoked": revoked},
        )
    return {"status": "ok"}


class GrantBody(BaseModel):
    plan_id: str
    duration_days: int = Field(gt=0, le=3650)
    notes: str = ""


@router.post("/users/{user_id}/grant", response_model=GrantResultDTO)
def grant_user_subscription(
    user_id: str,
    body: GrantBody,
    response: Response,
    ctx: AuthContext = Depends(require_admin_csrf),
    conn=Depends(platform_rw),
):
    _no_store(response)
    try:
        with tx(conn):
            result = grant_subscription(
                conn, user_id, body.plan_id, body.duration_days,
                granted_by=ctx.user_id, notes=body.notes,
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.post("/subscriptions/{subscription_id}/revoke", response_model=OkDTO)
def revoke_user_subscription(
    subscription_id: str,
    response: Response,
    ctx: AuthContext = Depends(require_admin_csrf),
    conn=Depends(platform_rw),
):
    _no_store(response)
    with tx(conn):
        ok = revoke_subscription(conn, subscription_id, ctx.user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="订阅不存在或已撤销")
    return {"status": "ok"}


@router.get("/audit-logs", response_model=AuditLogsResponse)
def list_audit_logs(
    response: Response,
    limit: int = 100,
    offset: int = 0,
    target_type: str = "",
    target_id: str = "",
    ctx: AuthContext = Depends(require_admin),
    conn=Depends(platform_ro),
):
    """target_type/target_id 可选(2026-08-16 新增):按具体对象查审计轨迹
    (如某张 reco_slip 的全部操作记录),不传即不筛选,数据本来就在
    audit_logs 里,这里只是补一个查询能力。"""
    _no_store(response)
    limit = max(1, min(limit, 500))
    rows = conn.execute(
        "SELECT * FROM audit_logs"
        " WHERE (?='' OR target_type=?) AND (?='' OR target_id=?)"
        " ORDER BY id DESC LIMIT ? OFFSET ?",
        (target_type, target_type, target_id, target_id, limit, offset),
    ).fetchall()
    return {"logs": [dict(r) for r in rows]}


# ── 任务健康 / 来源健康(只读,P1.5) ─────────────────────────────

@router.get("/jobs", response_model=AdminJobsResponse)
def list_jobs(
    response: Response,
    limit: int = 100,
    offset: int = 0,
    ctx: AuthContext = Depends(require_admin),
    conn=Depends(platform_ro),
):
    """platform.db job_runs 原始行(扁平分页,不做 job_name 聚合)。

    error_summary 复用 backend.cli.ops_check._sanitize_summary 脱敏
    (SQL/traceback/凭证/路径),不重新实现脱敏逻辑。
    """
    _no_store(response)
    limit = max(1, min(limit, 200))
    rows = conn.execute(
        """SELECT id, job_name, status, attempt, max_attempts, started_at, finished_at,
                  input_count, output_count, error_summary, created_at
           FROM job_runs ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?""",
        (limit, offset),
    ).fetchall()
    jobs = []
    for r in rows:
        job = dict(r)
        job["error_summary"] = _sanitize_summary(job["error_summary"])
        jobs.append(job)
    return {"jobs": jobs}


@router.get("/source-health", response_model=AdminSourceHealthResponse)
def list_source_health(
    response: Response,
    limit: int = 100,
    offset: int = 0,
    ctx: AuthContext = Depends(require_admin),
    conn=Depends(odds_ro),
):
    """odds.db source_health 原始行(扁平分页)。

    error_summary 同样复用 _sanitize_summary,不重新实现脱敏逻辑。
    """
    _no_store(response)
    limit = max(1, min(limit, 200))
    rows = conn.execute(
        """SELECT id, source, checked_at, ok, latency_ms, error_summary
           FROM source_health ORDER BY checked_at DESC, id DESC LIMIT ? OFFSET ?""",
        (limit, offset),
    ).fetchall()
    entries = []
    for r in rows:
        entry = dict(r)
        entry["error_summary"] = _sanitize_summary(entry["error_summary"])
        entries.append(entry)
    return {"source_health": entries}
