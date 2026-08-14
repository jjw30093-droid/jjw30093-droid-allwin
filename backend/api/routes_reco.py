"""「每日精选」端点(人工推荐板块;与模型预测路由彻底分开,CLAUDE.md §9.1 修订)。

可见性与缓存(设计:docs/design-reco-board.md,用户已确认):
- GET /api/v1/reco/daily        reco:daily(付费)      近 30 天推荐单
- GET /api/v1/reco/track-record reco:track_record(登录) 结算/作废归档 + 聚合
- /api/v1/admin/reco/*          admin + CSRF            录入/编辑/发布/结算/作废

全部路径**不进** PUBLIC_ALLOWLIST:中间件 default-deny 强制 private, no-store;
付费内容带 Cookie 请求同样触发强制 no-store——双层兜底,付费内容永不进共享缓存。
匿名请求 401(引导登录);已登录无权益 403 + reco_membership_required。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from backend.commands import reco as cmd
from backend.commands.reco import LegInput, RecoError
from backend.db.connections import tx
from backend.queries import odds as q_odds
from backend.queries import reco as q_reco

from .deps import (
    NO_STORE,
    AuthContext,
    core_ro,
    get_auth_context,
    odds_ro,
    platform_ro,
    platform_rw,
    require_csrf,
)
from .schemas import (
    AdminRecoSlipsResponse,
    OkDTO,
    RecoDailyResponse,
    RecoMatchCandidatesResponse,
    RecoMatchOddsOptionsResponse,
    RecoOverviewResponse,
    RecoSettleBody,
    RecoSettledDTO,
    RecoSlipCreateBody,
    RecoSlipCreatedDTO,
    RecoSlipEditBody,
    RecoTrackRecordResponse,
    RecoVoidBody,
    error_responses,
)

router = APIRouter(
    prefix="/api/v1",
    tags=["reco"],
    responses=error_responses(400, 401, 403, 404, 422),
)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = NO_STORE


def _require_entitlement(ctx: AuthContext, entitlement: str) -> None:
    if not ctx.authenticated:
        raise HTTPException(status_code=401, detail="请先登录")
    if not ctx.has(entitlement):
        raise HTTPException(
            status_code=403,
            detail={"code": "reco_membership_required", "entitlement": entitlement},
        )


# ── 匿名聚合面 ─────────────────────────────────────────────

@router.get("/reco/overview", response_model=RecoOverviewResponse)
def reco_overview(
    response: Response,
    conn=Depends(platform_ro),
):
    """匿名聚合面:今日发布场数/最近发布时间 + 近 30 天已结算汇总。

    只有计数与聚合,无任何单据内容(标题/场次/方向/赔率都不下发),
    供首页「今日精选/推荐战绩摘要」模块使用。未进 PUBLIC_ALLOWLIST,
    维持中间件 default-deny 的 no-store;首页服务端按 revalidate 自行缓存。
    """
    _no_store(response)
    return q_reco.public_overview(conn)


# ── 会员/付费只读面 ────────────────────────────────────────

@router.get("/reco/daily", response_model=RecoDailyResponse)
def reco_daily(
    response: Response,
    ctx: AuthContext = Depends(get_auth_context),
    conn=Depends(platform_ro),
):
    """付费面(reco:daily):近 30 天推荐单,含未结算(受限内容,物理不下发给无权益者)。"""
    _no_store(response)
    _require_entitlement(ctx, "reco:daily")
    return {"window_days": q_reco.RECO_DAILY_WINDOW_DAYS, "slips": q_reco.daily_slips(conn)}


@router.get("/reco/track-record", response_model=RecoTrackRecordResponse)
def reco_track_record(
    response: Response,
    limit: int = 50,
    offset: int = 0,
    ctx: AuthContext = Depends(get_auth_context),
    conn=Depends(platform_ro),
):
    """登录面(reco:track_record,member 基线):结算/作废归档全历史。

    命中/未中/走水全展示,作废单列不消失(对齐"不挑选、不隐藏");
    未结算 published 单不出现(赛前内容属付费面)。人工内容可修正,
    edit_count/last_edited_at 公开可查,不使用"锁定不可改"表述。
    """
    _no_store(response)
    _require_entitlement(ctx, "reco:track_record")
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    total, slips = q_reco.track_record_slips(conn, limit=limit, offset=offset)
    return {
        "summary": q_reco.track_record_summary(conn),
        "total": total,
        "slips": slips,
    }


# ── Admin 写面(admin + CSRF;全部操作写 audit_logs) ───────

def _require_admin(ctx: AuthContext) -> None:
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _legs(body_legs) -> list[LegInput]:
    return [
        LegInput(match_desc=l.match_desc, market=l.market, selection=l.selection,
                 odds=l.odds, match_id=l.match_id)
        for l in body_legs
    ]


@router.get("/admin/reco/slips", response_model=AdminRecoSlipsResponse, tags=["admin"])
def admin_list_slips(
    response: Response,
    limit: int = 100,
    offset: int = 0,
    ctx: AuthContext = Depends(get_auth_context),
    conn=Depends(platform_ro),
):
    _no_store(response)
    if not ctx.authenticated:
        raise HTTPException(status_code=401, detail="请先登录")
    _require_admin(ctx)
    total, slips = q_reco.admin_slips(conn, limit=max(1, min(limit, 200)), offset=max(0, offset))
    return {"total": total, "slips": slips}


@router.get(
    "/admin/reco/match-candidates", response_model=RecoMatchCandidatesResponse, tags=["admin"]
)
def admin_reco_match_candidates(
    response: Response,
    q: str | None = Query(None, min_length=1, max_length=80),
    limit: int = Query(20, ge=1, le=50),
    ctx: AuthContext = Depends(get_auth_context),
    conn=Depends(core_ro),
):
    """录入每日精选时的比赛候选(从真实比赛选,不再手打描述)。

    admin 身份即可见全部联赛的未开赛比赛,不受个人 Plan/Entitlement 门禁
    约束(§CLAUDE.md 8.1:Role 与 Plan 分离——建正式公开内容不能被自己的
    免费档位挡住看不到完整数据)。`q` 缺省时返回最近开球的一批,供不搜索
    直接浏览挑选。
    """
    _no_store(response)
    if not ctx.authenticated:
        raise HTTPException(status_code=401, detail="请先登录")
    _require_admin(ctx)
    return {"matches": q_reco.admin_match_candidates(conn, query=q, limit=limit)}


@router.get(
    "/admin/reco/match-candidates/{match_id}/odds-options",
    response_model=RecoMatchOddsOptionsResponse,
    tags=["admin"],
)
def admin_reco_match_odds_options(
    match_id: int,
    response: Response,
    ctx: AuthContext = Depends(get_auth_context),
    conn_odds=Depends(odds_ro),
):
    """选定比赛后的真实盘口选项(1x2/大小球/角球大小),供选择而非手打赔率。

    没有真实数据时 `options` 为空列表(不是 404)——前端据此退回手动输入,
    不把"抓不到"伪装成"这场没有对应市场"。
    """
    _no_store(response)
    if not ctx.authenticated:
        raise HTTPException(status_code=401, detail="请先登录")
    _require_admin(ctx)
    return {"match_id": match_id, "options": q_odds.raw_market_options(conn_odds, match_id)}


@router.post("/admin/reco/slips", response_model=RecoSlipCreatedDTO, tags=["admin"])
def admin_create_slip(
    body: RecoSlipCreateBody,
    response: Response,
    ctx: AuthContext = Depends(require_csrf),
    conn=Depends(platform_rw),
):
    _no_store(response)
    _require_admin(ctx)
    try:
        with tx(conn):
            slip_id = cmd.create_slip(
                conn, slip_date=body.slip_date, title=body.title,
                legs=_legs(body.legs), note=body.note, actor=ctx.user_id,
            )
    except RecoError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": slip_id}


@router.patch("/admin/reco/slips/{slip_id}", response_model=OkDTO, tags=["admin"])
def admin_edit_slip(
    slip_id: str,
    body: RecoSlipEditBody,
    response: Response,
    ctx: AuthContext = Depends(require_csrf),
    conn=Depends(platform_rw),
):
    _no_store(response)
    _require_admin(ctx)
    try:
        with tx(conn):
            cmd.edit_slip(
                conn, slip_id, actor=ctx.user_id, title=body.title, note=body.note,
                slip_date=body.slip_date,
                legs=_legs(body.legs) if body.legs is not None else None,
            )
    except RecoError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok"}


@router.post("/admin/reco/slips/{slip_id}/publish", response_model=OkDTO, tags=["admin"])
def admin_publish_slip(
    slip_id: str,
    response: Response,
    ctx: AuthContext = Depends(require_csrf),
    conn=Depends(platform_rw),
):
    _no_store(response)
    _require_admin(ctx)
    try:
        with tx(conn):
            cmd.publish_slip(conn, slip_id, actor=ctx.user_id)
    except RecoError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok"}


@router.post("/admin/reco/slips/{slip_id}/settle", response_model=RecoSettledDTO, tags=["admin"])
def admin_settle_slip(
    slip_id: str,
    body: RecoSettleBody,
    response: Response,
    ctx: AuthContext = Depends(require_csrf),
    conn=Depends(platform_rw),
):
    _no_store(response)
    _require_admin(ctx)
    try:
        with tx(conn):
            out = cmd.settle_slip(conn, slip_id, dict(body.leg_results), actor=ctx.user_id)
    except RecoError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return out


@router.post("/admin/reco/slips/{slip_id}/void", response_model=OkDTO, tags=["admin"])
def admin_void_slip(
    slip_id: str,
    body: RecoVoidBody,
    response: Response,
    ctx: AuthContext = Depends(require_csrf),
    conn=Depends(platform_rw),
):
    _no_store(response)
    _require_admin(ctx)
    try:
        with tx(conn):
            cmd.void_slip(conn, slip_id, actor=ctx.user_id, reason=body.reason)
    except RecoError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok"}
