"""/api/v1/admin/*:用户/订阅/审计日志/预测登记簿/任务与来源健康
(写操作全部过 CSRF + AuditLog;任务与来源健康只读)。
"""

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

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
    AdminPredictionsResponse,
    AdminSourceHealthResponse,
    AdminUsersResponse,
    AuditLogsResponse,
    EditPredictionResponse,
    GrantResultDTO,
    OkDTO,
    PublishUpcomingResponse,
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
    like = f"%{query}%"
    rows = conn.execute(
        """SELECT u.id, u.display_name, u.role, u.status, u.created_at, u.last_login_at,
                  (SELECT s.plan_id FROM subscriptions s JOIN plans p ON p.id=s.plan_id
                   WHERE s.user_id=u.id AND s.status='active' AND s.starts_at<=? AND s.ends_at>?
                   ORDER BY p.rank DESC, s.ends_at DESC LIMIT 1) AS plan_id,
                  (SELECT MAX(s.ends_at) FROM subscriptions s
                   WHERE s.user_id=u.id AND s.status='active' AND s.ends_at>?) AS plan_ends_at
           FROM users u
           WHERE (? = '' OR u.display_name LIKE ? OR u.id LIKE ?)
           ORDER BY u.created_at DESC LIMIT ? OFFSET ?""",
        (now, now, now, query, like, like, limit, offset),
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    return {
        "total": total,
        "users": [dict(r) | {"plan_id": r["plan_id"] or "free"} for r in rows],
    }


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


# ── 预测登记簿管理(P0.5) ─────────────────────────────────

@router.get("/predictions", response_model=AdminPredictionsResponse)
def list_predictions(
    response: Response,
    status: str = "",
    limit: int = 100,
    offset: int = 0,
    ctx: AuthContext = Depends(require_admin),
    conn=Depends(platform_ro),
):
    _no_store(response)
    limit = max(1, min(limit, 500))
    rows = conn.execute(
        """SELECT id, match_id, kickoff_at_utc, model_version_id, generated_at, published_at,
                  locked_at, status, is_official, visibility, home_win, draw, away_win, confidence,
                  edit_count, last_edited_at
           FROM prediction_snapshots WHERE (?='' OR status=?)
           ORDER BY kickoff_at_utc, match_id LIMIT ? OFFSET ?""",
        (status, status, limit, offset),
    ).fetchall()
    counts = conn.execute(
        "SELECT status, COUNT(*) AS n FROM prediction_snapshots GROUP BY status"
    ).fetchall()
    return {"counts": {r["status"]: r["n"] for r in counts}, "predictions": [dict(r) for r in rows]}


def _prediction_action(conn, action_fn, snapshot_id, actor, **kw):
    from backend.commands.predictions import PredictionError

    try:
        with tx(conn):
            action_fn(conn, snapshot_id, actor, **kw)
    except PredictionError as e:
        raise HTTPException(status_code=409, detail={"code": e.reason, "message": str(e)})


@router.post("/predictions/{snapshot_id}/publish", response_model=OkDTO)
def publish_prediction(
    snapshot_id: str,
    response: Response,
    ctx: AuthContext = Depends(require_admin_csrf),
    conn=Depends(platform_rw),
):
    from backend.commands.predictions import publish_snapshot

    _no_store(response)
    _prediction_action(conn, publish_snapshot, snapshot_id, ctx.user_id)
    return {"status": "ok"}


@router.post("/predictions/{snapshot_id}/lock", response_model=OkDTO)
def lock_prediction(
    snapshot_id: str,
    response: Response,
    ctx: AuthContext = Depends(require_admin_csrf),
    conn=Depends(platform_rw),
):
    from backend.commands.predictions import lock_snapshot

    _no_store(response)
    _prediction_action(conn, lock_snapshot, snapshot_id, ctx.user_id)
    return {"status": "ok"}


class RetractBody(BaseModel):
    reason: str = Field(min_length=2, max_length=500)


@router.post("/predictions/{snapshot_id}/retract", response_model=OkDTO)
def retract_prediction(
    snapshot_id: str,
    body: RetractBody,
    response: Response,
    ctx: AuthContext = Depends(require_admin_csrf),
    conn=Depends(platform_rw),
):
    from backend.commands.predictions import retract_snapshot

    _no_store(response)
    _prediction_action(conn, retract_snapshot, snapshot_id, ctx.user_id, reason=body.reason)
    return {"status": "ok"}


class EditPredictionBody(BaseModel):
    reason: str = Field(min_length=2, max_length=500)
    home_win: float | None = None
    draw: float | None = None
    away_win: float | None = None
    expected_home_goals: float | None = None
    expected_away_goals: float | None = None
    confidence: str | None = None


@router.post("/predictions/{snapshot_id}/edit", response_model=EditPredictionResponse)
def edit_prediction(
    snapshot_id: str,
    body: EditPredictionBody,
    response: Response,
    ctx: AuthContext = Depends(require_admin_csrf),
    conn=Depends(platform_rw),
):
    """直接修正一条预测(任意状态、任意时刻,含已锁定/已开球——见 CLAUDE.md §9.1)。

    每次真正产生变化的修正都会写入 prediction_snapshot_edits(append-only),
    不是静默覆盖;结果里的 changed_fields 为空表示这次调用没有产生实质变化。
    """
    from backend.commands.predictions import PredictionError, edit_snapshot

    _no_store(response)
    kwargs = {
        k: v for k, v in body.model_dump().items() if k != "reason"
    }
    try:
        with tx(conn):
            result = edit_snapshot(conn, snapshot_id, ctx.user_id, reason=body.reason, **kwargs)
    except PredictionError as e:
        raise HTTPException(status_code=409, detail={"code": e.reason, "message": str(e)})
    return result


class PublishUpcomingBody(BaseModel):
    lock: bool = True


@router.post("/predictions/publish-upcoming", response_model=PublishUpcomingResponse)
def publish_upcoming(
    body: PublishUpcomingBody,
    response: Response,
    ctx: AuthContext = Depends(require_admin_csrf),
    conn=Depends(platform_rw),
):
    """批量:尝试发布全部 draft(可选同时锁定)。逐条独立事务,失败不拖累其余。

    候选集不再按 kickoff_at_utc 预筛(候选行可能因 date_only/unknown provenance 而
    kickoff_at_utc 为 NULL,仍应被尝试并拿到明确失败原因,而不是被 SQL 悄悄排除在外)——
    唯一验证入口是 publish_snapshot/lock_snapshot 内部的门禁,这里只负责收集结果。
    """
    from backend.commands.predictions import PredictionError, lock_snapshot, publish_snapshot

    _no_store(response)
    ids = [r[0] for r in conn.execute("SELECT id FROM prediction_snapshots WHERE status='draft'")]
    done, failed = 0, []
    for sid in ids:
        try:
            with tx(conn):
                publish_snapshot(conn, sid, ctx.user_id)
                if body.lock:
                    lock_snapshot(conn, sid, ctx.user_id)
            done += 1
        except PredictionError as e:
            failed.append({"id": sid, "reason": e.reason})
    return {"published": done, "failed": failed}


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
