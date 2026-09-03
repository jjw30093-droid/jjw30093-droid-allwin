"""「每日精选」端点(人工推荐板块;与模型预测路由彻底分开,CLAUDE.md §9.1 修订)。

可见性与缓存(2026-08-16 产品权限口径修正,经用户批准——CLAUDE.md §8 三段
可见性修订;取代旧的"reco:daily 付费全局布尔权益":持有 daily_picks 订阅
即可看近 30 天全部推荐单):
- GET  /api/v1/reco/daily                       登录       列表(按 slip 分别授权投影)
- GET  /api/v1/reco/daily/{slip_id}             登录+按场授权 正文(401/403/200)
- GET  /api/v1/reco/my-access                   登录       个人授权查询
- GET  /api/v1/reco/track-record                匿名可见   结算/作废归档 + 聚合
- GET  /api/v1/reco/public                      匿名可见   每日公推(完全公开,2026-09 新增)
- POST /api/v1/admin/reco/access-grants         admin+CSRF 授权
- POST /api/v1/admin/reco/access-grants/{id}/revoke admin+CSRF 撤销
- GET  /api/v1/admin/reco/access-grants         admin      授权记录列表(筛选+分页)
- /api/v1/admin/reco/slips 等                   admin+CSRF 录入/编辑/发布/结算/作废

每日精选是全站唯一需要 admin 授权才能访问的内容,必须按"用户 + 单条 slip"
授予(backend/commands/reco_access.py)——用户获得一场的权限不因此看到其它
场次。admin 角色本身不自动获得访问:/reco/daily(/{slip_id}) 与
/admin/reco/slips/{slip_id}/preview 是两个彻底独立的端点,后者是 admin
后台预览用,不受这里的按场授权约束,也不能让前者绕过按场授权。

每日公推(board='daily_public',2026-09 新增)是与每日精选并列、完全公开
的板块,管理端操作方式相同(建单/发布/结算/作废),只是多一个板块归属;
不需要也不能对公推单发放 reco_access_grants 授权(backend/commands/
reco_access.py::grant_access 会拒绝)。两个板块的 (match_id, market) 互斥
——同一盘口不得跨板块重复推荐,只提醒不拦截(见 queries/reco.py::
cross_board_market_conflicts)。

除 `/reco/public` 外,全部路径**不进** PUBLIC_ALLOWLIST:中间件 default-deny
强制 private, no-store;带 Cookie 请求同样触发强制 no-store——双层兜底,
每日精选内容永不进共享缓存。reco/daily(/{slip_id})、reco/my-access:匿名
401(引导登录);已登录但未获该 slip 授权 403(响应体只含中性说明,不含
正文任何字段)。`/reco/public` 是本文件唯一进入 PUBLIC_ALLOWLIST 的路径,
签名里不读任何身份,响应对匿名与登录用户完全一致。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from backend.commands import reco as cmd
from backend.commands import reco_access
from backend.commands.reco import LegInput, RecoError
from backend.commands.reco_access import RecoAccessError
from backend.db.connections import tx
from backend.queries import odds as q_odds
from backend.queries import reco as q_reco

from .cache_policy import PUBLIC_CACHE_SHORT
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
    AdminRecoAccessGrantsResponse,
    AdminRecoSlipsResponse,
    OkDTO,
    RecoAccessGrantBody,
    RecoAccessGrantDTO,
    RecoAccessRevokeBody,
    RecoDailyResponse,
    RecoMatchCandidatesResponse,
    RecoMatchOddsOptionsResponse,
    RecoMyAccessResponse,
    RecoOverviewResponse,
    RecoPublicResponse,
    RecoSettleBody,
    RecoSettledDTO,
    RecoSlipCreateBody,
    RecoSlipCreatedDTO,
    RecoSlipDTO,
    RecoSlipEditBody,
    RecoSlipEditResponse,
    RecoSlipPreviewResponse,
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


@router.get("/reco/public", response_model=RecoPublicResponse)
def reco_public(
    response: Response,
    conn=Depends(platform_ro),
):
    """每日公推(board='daily_public',2026-09 新增):完全公开、匿名可见,
    不需要登录——与「每日精选」并列的板块,签名里刻意不注入 AuthContext,
    不存在按身份分叉的任何分支,响应对所有人完全一致。这是本文件**唯一**
    进入 PUBLIC_ALLOWLIST 的路径(见 backend/api/cache_policy.py),其余全部
    reco 路径继续维持中间件 default-deny 的 no-store。
    """
    response.headers["Cache-Control"] = PUBLIC_CACHE_SHORT
    return {
        "window_days": q_reco.RECO_PUBLIC_WINDOW_DAYS,
        "slips": q_reco.public_slips(conn),
    }


# ── 登录面(每日精选按"用户 + 单条 slip"授权;2026-08-16 修订) ──────

@router.get("/reco/daily", response_model=RecoDailyResponse)
def reco_daily(
    response: Response,
    ctx: AuthContext = Depends(get_auth_context),
    conn=Depends(platform_ro),
):
    """列表面:近 30 天推荐单的存在性 + 状态对任何已登录用户可见;内容
    (标题/摘要/腿/思路说明)只对当前用户持有 active 按场授权的 slip 下发
    (access_required=false),否则只给中性投影(access_required=true)。
    列表本身也是"每日精选权限查询"的一种,匿名 401,已登录即可查看。"""
    _no_store(response)
    if not ctx.authenticated:
        raise HTTPException(status_code=401, detail="请先登录")
    return {
        "window_days": q_reco.RECO_DAILY_WINDOW_DAYS,
        "slips": q_reco.daily_slips(conn, ctx.user_id),
    }


@router.get("/reco/daily/{slip_id}", response_model=RecoSlipDTO)
def reco_daily_slip(
    slip_id: str,
    response: Response,
    ctx: AuthContext = Depends(get_auth_context),
    conn=Depends(platform_ro),
):
    """正文访问(按场授权的硬性契约):未登录 401;slip 不存在或仍是 draft
    404(draft 永不外泄给普通用户面);已登录但对该 slip 没有 active 授权
    403(响应体只有中性 code/slip_id,不含任何正文字段);已登录且有 active
    授权 200,完整正文。admin 角色本身不因为是 admin 而绕过——这里与
    /admin/reco/slips/{slip_id}/preview 是两个彻底独立的端点。"""
    _no_store(response)
    if not ctx.authenticated:
        raise HTTPException(status_code=401, detail="请先登录")
    slip = q_reco.daily_slip_detail(conn, slip_id)
    if slip is None:
        raise HTTPException(status_code=404, detail="推荐单不存在")
    if not reco_access.has_access(conn, ctx.user_id, slip_id):
        raise HTTPException(
            status_code=403,
            detail={"code": "reco_access_required", "slip_id": slip_id},
        )
    return slip


@router.get("/reco/my-access", response_model=RecoMyAccessResponse)
def reco_my_access(
    response: Response,
    ctx: AuthContext = Depends(get_auth_context),
    conn=Depends(platform_ro),
):
    """个人"每日精选权限查询"(CLAUDE.md §8.1 允许要求登录的账户类个人
    功能之一):只列出当前用户自己的按场授权记录(含历史撤销),不含其他
    用户的任何信息。"""
    _no_store(response)
    if not ctx.authenticated:
        raise HTTPException(status_code=401, detail="请先登录")
    return {"grants": reco_access.list_user_grants(conn, ctx.user_id)}


@router.get("/reco/track-record", response_model=RecoTrackRecordResponse)
def reco_track_record(
    response: Response,
    limit: int = 50,
    offset: int = 0,
    conn=Depends(platform_ro),
):
    """匿名可见(2026-08-16 起,不再要求登录):结算/作废归档全历史。

    命中/未中/走水全展示,作废单列不消失(对齐"不挑选、不隐藏");
    未结算 published 单不出现(赛前内容属每日精选按场授权面)。这是站点
    自身的历史运营记录,与 backend/queries/track_record.py 里模型预测的
    公开战绩端点同一先例,不属于"每日精选"按场授权的约束范围。人工内容
    可修正,edit_count/last_edited_at 公开可查,不使用"锁定不可改"表述。
    """
    _no_store(response)
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
        LegInput(
            match_desc=l.match_desc, market=l.market, selection=l.selection,
            odds=l.odds, match_id=l.match_id,
            source_odds=l.source_odds, odds_format=l.odds_format,
            provider=l.provider, company_id=l.company_id, company_name=l.company_name,
            snapshot_ref=l.snapshot_ref, observed_at=l.observed_at, line=l.line,
            side=l.side, payload_hash=l.payload_hash,
        )
        for l in body_legs
    ]


@router.get("/admin/reco/slips", response_model=AdminRecoSlipsResponse, tags=["admin"])
def admin_list_slips(
    response: Response,
    limit: int = 100,
    offset: int = 0,
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    board: str = "",
    ctx: AuthContext = Depends(get_auth_context),
    conn=Depends(platform_ro),
    conn_core=Depends(core_ro),
):
    """status/date_from/date_to(按 slip_date)/board 均可选,不传即不筛选;
    total 是筛选后的计数,不是全库总数。board 不传返回两个板块(每日精选/
    每日公推,2026-09 新增)。"""
    _no_store(response)
    if not ctx.authenticated:
        raise HTTPException(status_code=401, detail="请先登录")
    _require_admin(ctx)
    total, slips = q_reco.admin_slips(
        conn, conn_core, limit=max(1, min(limit, 200)), offset=max(0, offset),
        status=status, date_from=date_from, date_to=date_to, board=board,
    )
    return {"total": total, "slips": slips}


@router.get(
    "/admin/reco/slips/{slip_id}/preview", response_model=RecoSlipPreviewResponse, tags=["admin"]
)
def admin_preview_slip(
    slip_id: str,
    response: Response,
    ctx: AuthContext = Depends(get_auth_context),
    conn=Depends(platform_ro),
):
    """会员视角预览:这张单如果是 published 状态,一个真实会员在 /reco/daily
    会看到的内容形状——直接复用 daily_slips() 同一套投影(q_reco.
    slip_member_preview),不重新发明会员端字段投影逻辑。draft 也可预览
    (admin 需要在发布前看)。"""
    _no_store(response)
    if not ctx.authenticated:
        raise HTTPException(status_code=401, detail="请先登录")
    _require_admin(ctx)
    slip = q_reco.slip_member_preview(conn, slip_id)
    if slip is None:
        raise HTTPException(status_code=404, detail="推荐单不存在")
    return {"slip": slip}


@router.get(
    "/admin/reco/match-candidates", response_model=RecoMatchCandidatesResponse, tags=["admin"]
)
def admin_reco_match_candidates(
    response: Response,
    q: str | None = Query(None, min_length=1, max_length=80),
    limit: int = Query(20, ge=1, le=50),
    window: str | None = Query(
        q_reco.ADMIN_MATCH_CANDIDATES_DEFAULT_WINDOW,
        pattern="^(today|tomorrow|3d|7d|all)$",
    ),
    ctx: AuthContext = Depends(get_auth_context),
    conn=Depends(core_ro),
):
    """录入每日精选时的比赛候选(从真实比赛选,不再手打描述)。

    admin 身份即可见全部联赛的未开赛比赛,不受个人 Plan/Entitlement 门禁
    约束(§CLAUDE.md 8.1:Role 与 Plan 分离——建正式公开内容不能被自己的
    免费档位挡住看不到完整数据)。`q` 缺省时返回最近开球的一批,供不搜索
    直接浏览挑选。

    `window` 默认 7 天,值域与语义完全复用 GET /api/v1/matches 已经在用的
    window 参数(today/tomorrow/3d/7d/all),不发明新的窗口表示法;传
    window=all 可以显式搜到 7 天窗口外(含没有精确 kickoff_at_utc)的比赛。
    """
    _no_store(response)
    if not ctx.authenticated:
        raise HTTPException(status_code=401, detail="请先登录")
    _require_admin(ctx)
    return {
        "matches": q_reco.admin_match_candidates(conn, query=q, limit=limit, window=window)
    }


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


def _conflict_warnings(conn, slip_id: str) -> list[dict]:
    """建单/改单后,读取这张单当前(post-write)真实的 board + 全部腿,查跨
    板块盘口冲突(2026-09,只提醒不拦截)。读的是写入后的落库真相而不是
    请求体本身,不管这次调用改没改 board/legs 都能拿到正确的当前状态。"""
    row = conn.execute("SELECT board FROM reco_slips WHERE id=?", (slip_id,)).fetchone()
    leg_rows = conn.execute(
        "SELECT match_id, market FROM reco_legs WHERE slip_id=?", (slip_id,)
    ).fetchall()
    return q_reco.cross_board_market_conflicts(
        conn, board=row["board"],
        legs=[{"match_id": r["match_id"], "market": r["market"]} for r in leg_rows],
        exclude_slip_id=slip_id,
    )


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
                board=body.board,
            )
            warnings = _conflict_warnings(conn, slip_id)
    except RecoError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": slip_id, "warnings": warnings}


@router.patch("/admin/reco/slips/{slip_id}", response_model=RecoSlipEditResponse, tags=["admin"])
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
                slip_date=body.slip_date, board=body.board,
                legs=_legs(body.legs) if body.legs is not None else None,
            )
            warnings = _conflict_warnings(conn, slip_id)
    except RecoError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "warnings": warnings}


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
            cmd.require_provenance_bound_legs(conn, slip_id)
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


# ── Admin 按场授权写面(2026-08-16 新增,取代旧的 reco:daily 全局布尔权益;
#    admin + CSRF;全部操作写 audit_logs)──────────────────────────────

@router.post("/admin/reco/access-grants", response_model=RecoAccessGrantDTO, tags=["admin"])
def admin_grant_reco_access(
    body: RecoAccessGrantBody,
    response: Response,
    ctx: AuthContext = Depends(require_csrf),
    conn=Depends(platform_rw),
):
    """按"用户 + 单条 slip"授权(幂等:同一 (user_id, slip_id) 已有 active
    授权时直接返回既有记录,不重复插入)。"""
    _no_store(response)
    _require_admin(ctx)
    try:
        with tx(conn):
            grant_id = reco_access.grant_access(
                conn, body.user_id, body.slip_id, actor=ctx.user_id, note=body.note,
            )
    except RecoAccessError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return reco_access.get_grant(conn, grant_id)


@router.post(
    "/admin/reco/access-grants/{grant_id}/revoke", response_model=OkDTO, tags=["admin"]
)
def admin_revoke_reco_access(
    grant_id: str,
    body: RecoAccessRevokeBody,
    response: Response,
    ctx: AuthContext = Depends(require_csrf),
    conn=Depends(platform_rw),
):
    _no_store(response)
    _require_admin(ctx)
    try:
        with tx(conn):
            reco_access.revoke_access(conn, grant_id, actor=ctx.user_id, reason=body.reason)
    except RecoAccessError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok"}


@router.get(
    "/admin/reco/access-grants", response_model=AdminRecoAccessGrantsResponse, tags=["admin"]
)
def admin_list_reco_access(
    response: Response,
    user_id: str = "",
    slip_id: str = "",
    status: str = "",
    limit: int = 100,
    offset: int = 0,
    ctx: AuthContext = Depends(get_auth_context),
    conn=Depends(platform_ro),
):
    """user_id/slip_id/status 均可选,不传即不筛选;total 是筛选后的计数,
    不是全库总数(与 admin_list_slips/list_codes 同一惯例)。"""
    _no_store(response)
    if not ctx.authenticated:
        raise HTTPException(status_code=401, detail="请先登录")
    _require_admin(ctx)
    total, grants = reco_access.list_grants(
        conn, user_id=user_id, slip_id=slip_id, status=status,
        limit=max(1, min(limit, 200)), offset=max(0, offset),
    )
    return {"total": total, "grants": grants}
