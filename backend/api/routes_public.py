"""/api/v1 公开数据端点(匿名可访问;付费差异由 DTO 投影与 entitlement 决定)。

缓存边界(CLAUDE.md §10.2):匿名数据响应给 s-maxage;prediction 端点因随
entitlement 变化,一律 private, no-store,防共享缓存泄漏付费字段。
"""

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from backend.content_status import (
    configured_free_outcome,
    load_content_status,
    public_status_for_match,
)
from backend.queries import league_stats as q_league_stats
from backend.queries import matches as q_matches
from backend.queries import odds as q_odds
from backend.queries import track_record as q_track
from backend.queries.leagues import (
    LEAGUE_META,
    accessible_league_ids,
    league_data_profiles,
)
from backend.queries.predictions import current_public_snapshot
from backend.queries.teams import team_display_map

from .deps import NO_STORE, AuthContext, core_ro, get_auth_context, odds_ro, platform_ro
from .schemas import (
    AnalysisBundleDTO,
    CooccurrenceResponse,
    LeagueFixturesResponse,
    LeagueInfo,
    MatchDetailResponse,
    MatchListResponse,
    MatchOddsResponse,
    ModelMetricsResponse,
    PredictionFreeDTO,
    PredictionFullDTO,
    PredictionMeta,
    PlayersResponse,
    PredictionResponse,
    ProductsResponse,
    StandingsResponse,
    TeamStatsResponse,
    TrackRecordMetrics,
    TrackRecordResponse,
    TrackRecordSample,
    error_responses,
)

router = APIRouter(
    prefix="/api/v1",
    tags=["public"],
    responses=error_responses(400, 401, 403, 404, 422),
)

PUBLIC_CACHE = "public, s-maxage=300, stale-while-revalidate=60"
PUBLIC_CACHE_SHORT = "public, s-maxage=60, stale-while-revalidate=30"


def _with_content_status(match: dict) -> dict:
    status = public_status_for_match(
        match["match_id"],
        kickoff_at_utc=match.get("kickoff_at_utc"),
    )
    if not status:
        return dict(match) | {"sync_state": "UNAVAILABLE"}
    projected = dict(match)
    projected.update(status)
    projected["sync_state"] = projected.pop("state")
    return projected


def _require_league_access(ctx: AuthContext, league_id: int) -> None:
    meta = LEAGUE_META.get(league_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="未知联赛")
    if not ctx.has(meta["entitlement"]):
        raise HTTPException(
            status_code=403 if ctx.authenticated else 401,
            detail={"code": "membership_required", "entitlement": meta["entitlement"]},
        )


# ── 联赛 ───────────────────────────────────────────────────

@router.get("/leagues", response_model=list[LeagueInfo])
def list_leagues(
    response: Response,
    ctx: AuthContext = Depends(get_auth_context),
    conn=Depends(core_ro),
):
    # accessible 随请求者身份变化 → 不进共享缓存
    response.headers["Cache-Control"] = NO_STORE
    profiles = league_data_profiles(conn)
    durable_status = load_content_status()
    return [
        LeagueInfo(
            league_id=lid,
            code=m["code"],
            name_zh=m["name_zh"],
            name_en=m["name_en"],
            entitlement=m["entitlement"],
            accessible=ctx.has(m["entitlement"]),
            requires_pro=m["entitlement"] == "league:top5",
            current_season=profiles[lid]["current_season"],
            available_seasons=q_matches.seasons_of_league(conn, lid),
            data_status=profiles[lid]["data_status"],
            data_updated_at=(
                durable_status.get("last_success_sync_at")
                if durable_status.get("league_id") == lid
                else profiles[lid]["data_updated_at"]
            ),
        )
        for lid, m in LEAGUE_META.items()
    ]


@router.get(
    "/leagues/{league_id}/standings",
    response_model=StandingsResponse,
    # empty_reason 只在无数据时出现;exclude_unset 保持响应键集与原 dict 一致
    response_model_exclude_unset=True,
)
def league_standings(
    league_id: int,
    response: Response,
    season: str | None = None,
    ctx: AuthContext = Depends(get_auth_context),
    conn=Depends(core_ro),
):
    _require_league_access(ctx, league_id)
    response.headers["Cache-Control"] = PUBLIC_CACHE if league_id == 47 else NO_STORE
    data = q_matches.standings(conn, league_id, season)
    if not data["rows"]:
        data["empty_reason"] = "该联赛暂无积分榜数据"
    return {"league_id": league_id, **data}


@router.get("/leagues/{league_id}/fixtures", response_model=LeagueFixturesResponse)
def league_fixtures(
    league_id: int,
    response: Response,
    season: str | None = None,
    status: str | None = Query(None, pattern="^(upcoming|finished)$"),
    limit: int = Query(100, ge=1, le=400),
    offset: int = Query(0, ge=0),
    ctx: AuthContext = Depends(get_auth_context),
    conn=Depends(core_ro),
):
    _require_league_access(ctx, league_id)
    response.headers["Cache-Control"] = PUBLIC_CACHE if league_id == 47 else NO_STORE
    seasons = q_matches.seasons_of_league(conn, league_id)
    # 未显式传 season,或传的赛季库里没有 → 取"最早一场未开赛比赛"所在赛季
    # (不是 max(Season)):英超 2026/2027 已有赛程但还没打过比赛,max(Season)
    # 会命中它没错;但对零场未开赛的联赛(西甲/意甲/法甲/德甲当前实况)
    # max(Season) 会命中一个同样没有未开赛比赛的"最新"赛季——两种情况问的
    # 都是同一个问题:"现在最该看哪个赛季",不是字符串意义上最大的那个。
    # 不再对未知赛季返回 400:与 standings/team-stats/players 三个端点统一
    # 策略,静默回退并由响应体的 season 字段如实告知用户实际看到的是哪个。
    if season is None or season not in seasons:
        season = q_matches.default_fixture_season(conn, league_id, seasons)
    # 赛季过滤下推进 SQL(list_matches 的 season 参数):曾在此处做 Python 后筛,
    # 多赛季联赛下 SQL LIMIT 先截走其它赛季的行,目标赛季可能 0 命中
    result = q_matches.list_matches(
        conn, {league_id}, status=status, season=season, limit=limit, offset=offset
    )
    result["matches"] = [_with_content_status(match) for match in result["matches"]]
    if not result["matches"]:
        result["empty_reason"] = "该联赛该赛季暂无赛程数据"
    return {
        "league_id": league_id,
        "season": season,
        "available_seasons": seasons,
        "limit": limit,
        "offset": offset,
        **result,
    }


@router.get(
    "/leagues/{league_id}/team-stats",
    response_model=TeamStatsResponse,
    response_model_exclude_unset=True,
)
def league_team_stats(
    league_id: int,
    response: Response,
    season: str | None = None,
    ctx: AuthContext = Depends(get_auth_context),
    conn=Depends(core_ro),
):
    """球队赛季统计(免费字段投影:射门/射正/控球/xG/xGOT,付费深度字段物理不在响应)。"""
    _require_league_access(ctx, league_id)
    response.headers["Cache-Control"] = PUBLIC_CACHE if league_id == 47 else NO_STORE
    data = q_league_stats.team_season_stats(conn, league_id, season)
    if not data["rows"]:
        data["empty_reason"] = "该联赛暂无球队赛季统计数据"
    return {"league_id": league_id, **data}


@router.get(
    "/leagues/{league_id}/players",
    response_model=PlayersResponse,
    response_model_exclude_unset=True,
)
def league_players(
    league_id: int,
    response: Response,
    season: str | None = None,
    ctx: AuthContext = Depends(get_auth_context),
    conn=Depends(core_ro),
):
    """球员榜(免费 5 维度:进球/助攻/xG/xGOT/评分,各 top 10)。"""
    _require_league_access(ctx, league_id)
    response.headers["Cache-Control"] = PUBLIC_CACHE if league_id == 47 else NO_STORE
    data = q_league_stats.player_leaderboards(conn, league_id, season)
    if not any(board["entries"] for board in data["boards"]):
        data["empty_reason"] = "该联赛该赛季暂无球员榜数据"
    return {"league_id": league_id, **data}


# ── 比赛 ───────────────────────────────────────────────────

@router.get("/matches", response_model=MatchListResponse)
def list_matches(
    response: Response,
    date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    league_id: int | None = None,
    # 赛季筛选:库里 5 大联赛各有 6 个历史赛季(2020/2021..2025/2026)+ 下赛季赛程,
    # 查询层 q_matches.list_matches 早就支持 season 下推,但这里一直没透出,
    # 导致全站比赛列表只能看"最近 N 天",10,735 场已完赛比赛在本页无路可达。
    # 自然年赛季联赛(挪超/瑞超)是 "2026" 形式,所以放宽到两种写法都接受。
    season: str | None = Query(None, pattern=r"^\d{4}(/\d{4})?$"),
    status: str | None = Query(None, pattern="^(upcoming|finished)$"),
    window: str | None = Query(None, pattern="^(today|tomorrow|3d|7d|all)$"),
    content: str | None = Query(None, pattern="^(analysis|odds)$"),
    q: str | None = Query(None, min_length=1, max_length=80),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: AuthContext = Depends(get_auth_context),
    conn=Depends(core_ro),
    conn_platform=Depends(platform_ro),
    conn_odds=Depends(odds_ro),
):
    """比赛列表:只返回请求者有权限的联赛(免费=英超)。"""
    response.headers["Cache-Control"] = NO_STORE if ctx.authenticated else PUBLIC_CACHE_SHORT
    allowed = accessible_league_ids(ctx.entitlements)
    query_team_ids: set[int] = set()
    if q:
        normalized_query = " ".join(q.lower().split())
        try:
            query_team_ids = {
                int(row[0])
                for row in conn_odds.execute(
                    "SELECT canonical_team_id FROM dim_team_alias WHERE alias=?",
                    (normalized_query,),
                )
            }
        except sqlite3.OperationalError:
            query_team_ids = set()
    try:
        analysis_match_ids = {
            int(row[0])
            for row in conn_platform.execute(
                """SELECT DISTINCT match_id FROM prediction_snapshots
                     WHERE status IN ('published','locked')"""
            )
        }
    except sqlite3.OperationalError:
        analysis_match_ids = set()
    # "有赔率"= 完整时间线(bronze_ng_odds_snap,经 xref)∪ 旧项目两点摘要
    # (bronze_legacy_odds_summary,直接以 fotmob_match_id 为键)。只算前者会漏掉
    # 8,336 场只有两点摘要的比赛——它们在比赛详情页确实能看到赔率,
    # 却被 content=odds 筛选排除,属于筛选口径与实际可见内容不一致。
    try:
        odds_match_ids = {
            int(row[0])
            for row in conn_odds.execute(
                """SELECT DISTINCT x.fotmob_match_id
                     FROM dim_match_xref x
                     JOIN bronze_ng_odds_snap b
                       ON b.provider_match_id=x.provider_match_id
                    WHERE x.provider='nowgoal'
                      AND x.review_status IN ('auto_ok','confirmed')"""
            )
        }
    except sqlite3.OperationalError:
        odds_match_ids = set()
    try:
        odds_match_ids |= {
            int(row[0])
            for row in conn_odds.execute(
                "SELECT DISTINCT fotmob_match_id FROM bronze_legacy_odds_summary"
            )
        }
    except sqlite3.OperationalError:
        pass
    match_ids = (
        analysis_match_ids
        if content == "analysis"
        else odds_match_ids
        if content == "odds"
        else None
    )
    result = q_matches.list_matches(
        conn,
        allowed,
        date=date,
        status=status,
        league_id=league_id,
        season=season,
        window=window,
        query=q,
        query_team_ids=query_team_ids,
        match_ids=match_ids,
        priority_match_ids=analysis_match_ids | odds_match_ids,
        limit=limit,
        offset=offset,
    )
    result["matches"] = [_with_content_status(match) for match in result["matches"]]
    # D8:逐场标注赔率覆盖档位(集合每请求算一次,不逐行查库);
    # full_timeline 优先于 open_close_only(同场两者皆有时按更高档展示)。
    full_set, legacy_set = q_odds.odds_coverage_sets(conn_odds)
    for match in result["matches"]:
        mid = match["match_id"]
        match["odds_coverage_tier"] = (
            "full_timeline"
            if mid in full_set
            else "open_close_only"
            if mid in legacy_set
            else "none"
        )
    # D4:回显赛季上下文,让 ?season= 的 0 结果可解释。
    if league_id is not None and league_id in allowed:
        available_seasons = q_matches.seasons_of_league(conn, league_id)
    else:
        seen: set[str] = set()
        for lid in sorted(allowed):
            seen.update(q_matches.seasons_of_league(conn, lid))
        available_seasons = sorted(seen)
    return {
        "limit": limit,
        "offset": offset,
        "season": season,
        "available_seasons": available_seasons,
        **result,
    }


@router.get("/matches/{match_id}", response_model=MatchDetailResponse)
def match_detail(
    match_id: int,
    response: Response,
    ctx: AuthContext = Depends(get_auth_context),
    conn=Depends(core_ro),
):
    m = q_matches.match_by_id(conn, match_id)
    if m is None:
        raise HTTPException(status_code=404, detail="比赛不存在")
    _require_league_access(ctx, m["league_id"])
    response.headers["Cache-Control"] = PUBLIC_CACHE_SHORT if m["league_id"] == 47 else NO_STORE
    m = _with_content_status(m)
    home_form = q_matches.recent_form(conn, m["home"]["team_id"], m["date_utc"])
    away_form = q_matches.recent_form(conn, m["away"]["team_id"], m["date_utc"])
    return {
        "match": m,
        "data_updated_at": m.get("data_updated_at"),
        "home_form": home_form,
        "away_form": away_form,
    }


# ── 预测(字段级门禁核心) ─────────────────────────────────

@router.get("/matches/{match_id}/prediction", response_model=PredictionResponse)
def match_prediction(
    match_id: int,
    response: Response,
    ctx: AuthContext = Depends(get_auth_context),
    conn_core=Depends(core_ro),
    conn_platform=Depends(platform_ro),
):
    """免费/匿名:只有最高一项概率(受限字段物理不存在);Pro/Premium:完整 WDL。

    响应随 entitlement 变化 → 永远 private, no-store,禁止共享缓存。
    """
    response.headers["Cache-Control"] = NO_STORE
    m = q_matches.match_by_id(conn_core, match_id)
    if m is None:
        raise HTTPException(status_code=404, detail="比赛不存在")
    _require_league_access(ctx, m["league_id"])

    snap = current_public_snapshot(conn_platform, match_id)
    if snap is None:
        return PredictionResponse(
            match_id=match_id, available=False, reason="该场比赛暂无已发布的正式预测"
        )
    if snap["status"] == "retracted":
        return PredictionResponse(
            match_id=match_id, available=False, reason="该预测已被撤回(登记簿中保留原记录)"
        )

    probs = {"home": snap["home_win"], "draw": snap["draw"], "away": snap["away_win"]}
    top_outcome = max(probs, key=probs.get)
    model_row = conn_platform.execute(
        "SELECT algorithm FROM model_versions WHERE id=?", (snap["model_version_id"],)
    ).fetchone()
    probability_source = (
        "MARKET_BASELINE"
        if model_row is not None and model_row["algorithm"] == "market_baseline"
        else "MODEL"
    )
    meta = PredictionMeta(
        model_version_id=snap["model_version_id"],
        probability_source=probability_source,
        generated_at=snap["generated_at"],
        published_at=snap["published_at"],
        locked_at=snap["locked_at"],
        input_cutoff_at=snap["input_cutoff_at"],
        status=snap["status"],
        confidence=snap["confidence"],
    )
    if ctx.has("prediction:full_wdl"):
        dto = PredictionFullDTO(
            top_outcome=top_outcome,
            home_probability=round(snap["home_win"], 4),
            draw_probability=round(snap["draw"], 4),
            away_probability=round(snap["away_win"], 4),
            expected_home_goals=snap["expected_home_goals"],
            expected_away_goals=snap["expected_away_goals"],
            prediction_hash=snap["prediction_hash"],
            meta=meta,
        )
    else:
        free_outcome = configured_free_outcome(match_id) or top_outcome
        dto = PredictionFreeDTO(
            top_outcome=free_outcome,
            top_probability=round(probs[free_outcome], 2),
            meta=meta,
        )
    return PredictionResponse(match_id=match_id, available=True, prediction=dto)


@router.get("/matches/{match_id}/analysis", response_model=AnalysisBundleDTO)
def match_analysis(
    match_id: int,
    response: Response,
    ctx: AuthContext = Depends(get_auth_context),
    conn_core=Depends(core_ro),
    conn_platform=Depends(platform_ro),
    conn_odds=Depends(odds_ro),
):
    """公开版 analysis_bundle:同一 bundle 按 entitlement 投影(与 Studio 共用生成逻辑)。

    - prediction_member / prob_bar 图表:仅 prediction:full_wdl;
    - odds_timeline:仅 odds:history_full;
    - cooccurring_events 明细:仅 report:deep(否则只给计数)。
    随 entitlement 变化 → private, no-store。
    """
    from backend.studio.bundle import build_analysis_bundle

    response.headers["Cache-Control"] = NO_STORE
    m = q_matches.match_by_id(conn_core, match_id)
    if m is None:
        raise HTTPException(status_code=404, detail="比赛不存在")
    _require_league_access(ctx, m["league_id"])
    bundle = build_analysis_bundle(conn_core, conn_platform, conn_odds, match_id)
    cooc_count = len(bundle["cooccurring_events"])
    if not ctx.has("prediction:full_wdl"):
        bundle["prediction_member"] = None
        bundle["chart_specs"] = [c for c in bundle["chart_specs"] if c["type"] != "probability_bar"]
        # counter_evidence 里的 draw_risk 条目原样嵌了真实平局概率(bundle.py 的
        # "draw_risk" 分支),不是只有 prediction_member/chart_specs 会泄漏受限数值。
        bundle["counter_evidence"] = [
            c for c in bundle["counter_evidence"] if c.get("kind") != "draw_risk"
        ]
        # 口播稿概率段含完整概率,免费层同样收敛为最高一项
        for sec in bundle["script_sections"]:
            if sec["id"] == "probability" and bundle["prediction_public"]:
                p = bundle["prediction_public"]
                zh = {"home": "主胜", "draw": "平局", "away": "客胜"}[p["top_outcome"]]
                source_label = (
                    "市场 1X2 去水基线"
                    if bundle["probability_source"] == "MARKET_BASELINE"
                    else "模型"
                )
                sec["text"] = (
                    f"{source_label}当前最高项:{zh}"
                    f"(概率 {round(p['top_probability'] * 100)}%)。"
                    "完整三项概率为会员内容。"
                )
            elif sec["id"] == "risk":
                # bundle.py 用未过滤的 counter_evidence 预渲染了这段文字(可能包含
                # 上面刚删掉的 draw_risk 数值),必须用过滤后的列表重新拼接,不能
                # 假设"数组级过滤"会自动反映到已经渲染成字符串的 script_sections 里。
                sec["text"] = (
                    "。".join(c["text"] for c in (bundle["counter_evidence"] + bundle["uncertainty"])[:3])
                    or "本场无特别突出的反向证据。"
                )
    if not ctx.has("odds:history_full"):
        bundle["odds_timeline"] = []
        # 两点摘要与完整时间线同属 odds:history_full 权益面:非 Premium 置 None,
        # 受限数据物理不下发(bundle 生成侧不做投影,与 prediction_member 同约定)
        bundle["odds_summary_points"] = None
    if not ctx.has("report:deep"):
        bundle["cooccurring_events"] = []
    bundle["cooccurrence_count"] = cooc_count
    bundle.pop("subtitle_cues", None)   # 页面用不到,字幕留给 Studio
    return bundle


# ── 赔率与同期事件 ─────────────────────────────────────────

@router.get("/matches/{match_id}/odds", response_model=MatchOddsResponse)
def match_odds(
    match_id: int,
    response: Response,
    ctx: AuthContext = Depends(get_auth_context),
    conn_core=Depends(core_ro),
    conn_odds=Depends(odds_ro),
):
    """free/pro:延迟摘要(每公司每市场最新一条,观察时间 ≥1 小时前);
    premium(odds:history_full):完整快照时间线。"""
    response.headers["Cache-Control"] = NO_STORE
    m = q_matches.match_by_id(conn_core, match_id)
    if m is None:
        raise HTTPException(status_code=404, detail="比赛不存在")
    _require_league_access(ctx, m["league_id"])

    # provider='nowgoal':本端点下游只查 bronze_ng_odds_snap(nowgoal 形状的表,
    # 无 provider 列)。dim_match_xref 的 UNIQUE 是 (provider, fotmob_match_id),
    # 同一场比赛可以同时有 nowgoal 与 kbisai 两条 xref——不加这个过滤,
    # .fetchone() 可能拿到 kbisai 行,provider_match_id 对不上 bronze_ng_odds_snap,
    # 结果是"已建立映射但查无快照"这句假话(真实原因是映射到了错的 provider)。
    xref = conn_odds.execute(
        "SELECT * FROM dim_match_xref WHERE fotmob_match_id=? AND provider='nowgoal'"
        " AND review_status IN ('auto_ok','confirmed')",
        (match_id,),
    ).fetchone()
    full = ctx.has("odds:history_full")
    if xref is None:
        return _legacy_odds_fallback(conn_odds, match_id, full)

    if full:
        rows = conn_odds.execute(
            """SELECT market, company_id, company_name, market_phase, payload_json,
                      source_updated_at, observed_at
               FROM bronze_ng_odds_snap WHERE provider_match_id=?
               ORDER BY market, company_id, observed_at""",
            (xref["provider_match_id"],),
        ).fetchall()
    else:
        from datetime import datetime, timedelta, timezone

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = conn_odds.execute(
            """SELECT market, company_id, company_name, market_phase, payload_json,
                      source_updated_at, MAX(observed_at) AS observed_at
               FROM bronze_ng_odds_snap
               WHERE provider_match_id=? AND observed_at <= ?
               GROUP BY market, company_id""",
            (xref["provider_match_id"], cutoff),
        ).fetchall()
    snapshots = [dict(r) | {"payload": json.loads(r["payload_json"])} for r in rows]
    for s in snapshots:
        s.pop("payload_json", None)
    if not snapshots:
        # 完整时间线口径下无快照 → 尝试旧项目两点摘要,再不行才 unavailable
        return _legacy_odds_fallback(
            conn_odds, match_id, full,
            no_data_reason="已建立映射,但暂无满足口径的赔率快照"
            + ("" if full else "(免费层延迟 1 小时)"),
        )
    observation_count = conn_odds.execute(
        """SELECT COUNT(DISTINCT observed_at) FROM bronze_ng_odds_snap
           WHERE provider_match_id=? AND market='1x2' AND market_phase='pre_match'""",
        (xref["provider_match_id"],),
    ).fetchone()[0]
    return {
        "match_id": match_id,
        "available": True,
        "tier": "full" if full else "delayed_summary",
        "coverage_tier": "full_timeline",
        "home_away_inverted": bool(xref["home_away_inverted"]),
        "observation_count": observation_count,
        "display_mode": "odds_changes" if observation_count >= 2 else "current_odds",
        "snapshots": snapshots,
    }


def _legacy_odds_fallback(conn_odds, match_id: int, full: bool,
                          no_data_reason: str = "该场比赛暂无已验证的赔率数据映射"):
    """旧项目两点摘要(bronze_legacy_odds_summary)兜底。

    数据已在入库时归一为 canonical 方向(方向修正见 backend/cli/ingest_legacy_odds.py),
    无逐条观测时间戳 → coverage_tier='open_close_only',前端不得画走势图。
    entitlement 投影:odds:history_full 给 initial+latest 两点(可看开临变化),
    否则只给 latest(与延迟摘要同级,服务端投影,不下发后遮挡)。
    """
    # SQL + 权益投影收口在 backend/queries/odds.py::legacy_summary_points,
    # 与 studio/bundle 共用——保证两处对同一场比赛看到完全相同的点集。
    rows = q_odds.legacy_summary_points(conn_odds, match_id, full)
    if not rows:
        return {"match_id": match_id, "available": False, "reason": no_data_reason}
    return {
        "match_id": match_id,
        "available": True,
        "tier": "full" if full else "delayed_summary",
        "coverage_tier": "open_close_only",
        "home_away_inverted": False,   # 入库时已归一为 canonical 方向
        "observation_count": 0,        # 无带时间戳的观测(§6.2:不伪装)
        "display_mode": "current_odds",
        "snapshots": [],
        "summary_points": rows,
        "note": "本场为历史存档赔率,仅有初盘与临场两个观测点,无完整走势时间线。",
    }


@router.get("/matches/{match_id}/cooccurrence", response_model=CooccurrenceResponse)
def match_cooccurrence(
    match_id: int,
    response: Response,
    ctx: AuthContext = Depends(get_auth_context),
    conn_core=Depends(core_ro),
    conn_odds=Depends(odds_ro),
):
    """同期事件(时间共现,不声称因果)。匿名给计数,report:deep 给明细。"""
    response.headers["Cache-Control"] = NO_STORE
    m = q_matches.match_by_id(conn_core, match_id)
    if m is None:
        raise HTTPException(status_code=404, detail="比赛不存在")
    _require_league_access(ctx, m["league_id"])
    total = conn_odds.execute(
        "SELECT COUNT(*) FROM gold_move_cooccurrence WHERE fotmob_match_id=?", (match_id,)
    ).fetchone()[0]
    if not ctx.has("report:deep"):
        return {"match_id": match_id, "count": total, "items": None,
                "note": "明细为 Pro 内容" if total else "暂无同期事件"}
    rows = conn_odds.execute(
        """SELECT c.window_seconds, c.delta_seconds, c.computed_at,
                  om.market, om.company_id, om.field, om.prev_value, om.new_value, om.moved_at AS odds_moved_at,
                  em.event_type, em.detail_json, em.moved_at AS event_moved_at
           FROM gold_move_cooccurrence c
           JOIN silver_odds_moves om ON om.id = c.odds_move_id
           JOIN silver_event_moves em ON em.id = c.event_move_id
           WHERE c.fotmob_match_id=? ORDER BY om.moved_at""",
        (match_id,),
    ).fetchall()
    return {"match_id": match_id, "count": total, "items": [dict(r) for r in rows]}


# ── 公开战绩与模型指标 ─────────────────────────────────────

@router.get("/track-record", response_model=TrackRecordResponse)
def track_record(
    response: Response,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn_platform=Depends(platform_ro),
    conn_core=Depends(core_ro),
):
    """匿名公开:全部正式样本(official + 曾锁定 + 赛前发布),不挑选。

    永久资格不变量(CLAUDE.md §9.1):撤回(retracted)与被修正版取代
    (superseded_by 非空)的正式样本不退出列表与指标分母,只带状态与修正链
    标注(status / superseded_by / correction_of);修正链新旧版本同时返回。
    """
    response.headers["Cache-Control"] = PUBLIC_CACHE
    data = q_track.official_samples(conn_platform, limit=limit, offset=offset)
    display = team_display_map(conn_core)
    samples = []
    for s in data["samples"]:
        m = conn_core.execute(
            "SELECT Home_Team_ID, Away_Team_ID, Home_Team_Name, Away_Team_Name FROM dim_match WHERE Match_ID=?",
            (s["match_id"],),
        ).fetchone()
        home = (
            q_matches._team_ref(m["Home_Team_ID"], m["Home_Team_Name"], display)
            if m
            else {"name": "未知"}
        )
        away = (
            q_matches._team_ref(m["Away_Team_ID"], m["Away_Team_Name"], display)
            if m
            else {"name": "未知"}
        )
        probs = {"home": s["home_win"], "draw": s["draw"], "away": s["away_win"]}
        predicted = max(probs, key=probs.get)
        samples.append(
            TrackRecordSample(
                snapshot_id=s["id"],
                match_id=s["match_id"],
                kickoff_at_utc=s["kickoff_at_utc"],
                home=home,
                away=away,
                home_probability=s["home_win"],
                draw_probability=s["draw"],
                away_probability=s["away_win"],
                predicted_outcome=predicted,
                actual_outcome=s["outcome"],
                home_goals=s["home_goals"],
                away_goals=s["away_goals"],
                hit=(s["outcome"] == predicted) if s["outcome"] else None,
                status=s["status"],
                superseded_by=s["superseded_by"],
                correction_of=s["correction_of"],
                superseded_note=(
                    "该版本已被修正版取代;新旧版本均保留并计入公开战绩与评估"
                    if s["superseded_by"] else None
                ),
                model_version_id=s["model_version_id"],
                published_at=s["published_at"],
                locked_at=s["locked_at"],
                prediction_hash=s["prediction_hash"],
            )
        )
    metrics = None
    ev = q_track.latest_evaluation(conn_platform)
    if ev:
        metrics = TrackRecordMetrics(
            sample_size=ev["sample_size"],
            accuracy=ev["accuracy"],
            brier=ev["brier"],
            log_loss=ev["log_loss"],
            rps=ev["rps"],
            evaluated_at=ev["evaluated_at"],
        )
    return TrackRecordResponse(
        total=data["total"],
        retracted_count=data["retracted_count"],
        superseded_count=data["superseded_count"],
        limit=limit,
        offset=offset,
        metrics=metrics,
        samples=samples,
        empty_reason=None if data["total"] else "暂无符合口径的正式样本(正式样本 = 开球前发布并锁定的预测)",
    )


@router.get("/model/metrics", response_model=ModelMetricsResponse)
def model_metrics(response: Response, conn=Depends(platform_ro)):
    """模型版本与评估口径。研发期指标来自 walk-forward 回测;正式战绩指标只来自
    正式样本(official + 曾锁定 + 赛前发布)的离线评估,分母含撤回与被取代版本,
    不可选择性剔除(CLAUDE.md §9.1)。市场(收盘赔率)基线 UNVERIFIED,不作比较声明。"""
    response.headers["Cache-Control"] = PUBLIC_CACHE
    versions = [
        dict(r) | {
            "params": json.loads(r["params_json"] or "{}"),
            "dev_metrics": json.loads(r["metrics_json"] or "{}"),
        }
        for r in conn.execute("SELECT * FROM model_versions ORDER BY created_at").fetchall()
    ]
    for v in versions:
        v.pop("params_json", None)
        v.pop("metrics_json", None)
    ev = q_track.latest_evaluation(conn)
    official = None
    if ev:
        official = {
            "sample_size": ev["sample_size"],
            "accuracy": ev["accuracy"],
            "brier": ev["brier"],
            "log_loss": ev["log_loss"],
            "rps": ev["rps"],
            "calibration": json.loads(ev["calibration_json"] or "[]"),
            "evaluated_at": ev["evaluated_at"],
        }
    return {
        "model_versions": versions,
        "official_evaluation": official,
        "official_evaluation_note": None if official else "暂无正式样本评估;研发期回测指标见各模型版本 dev_metrics",
        "market_baseline": {
            "status": "UNVERIFIED",
            "note": "尚未完成可复现的收盘赔率配对评估;现有对照仅为历史主/平/客频率基线",
        },
    }


# ── 产品(定价页数据源) ───────────────────────────────────

@router.get("/products", response_model=ProductsResponse)
def list_products(response: Response, conn=Depends(platform_ro)):
    """定价页数据源:plans + products 全部来自 DB,不在前端组件写死。"""
    response.headers["Cache-Control"] = PUBLIC_CACHE
    plans = conn.execute(
        "SELECT id, name_zh, description, rank FROM plans WHERE is_active=1 ORDER BY rank"
    ).fetchall()
    ents = conn.execute("SELECT plan_id, entitlement FROM plan_entitlements").fetchall()
    ent_map: dict[str, list] = {}
    for r in ents:
        ent_map.setdefault(r["plan_id"], []).append(r["entitlement"])
    products = conn.execute(
        "SELECT id, plan_id, name_zh, description, duration_days, price_cents, currency, sort_order"
        " FROM products WHERE is_active=1 ORDER BY sort_order"
    ).fetchall()
    return {
        "plans": [dict(p) | {"entitlements": sorted(ent_map.get(p["id"], []))} for p in plans],
        "products": [dict(p) for p in products],
    }
