"""/api/v1 公开数据端点。

2026-08-16 产品权限口径修正(经用户批准):除"每日精选"外,网站所有比赛
内容全部免费,包括匿名用户——登录与内容分层彻底解耦,本文件的联赛/比赛级
端点不再有任何 entitlement 门禁或按登录状态投影的字段裁剪。

缓存边界(CLAUDE.md §10.2):匿名数据响应给 s-maxage;prediction/analysis/
odds/cooccurrence 端点内容会随时间更新(预测发布/赔率刷新),一律
private, no-store,不进共享缓存(与登录状态无关)。
"""

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from backend.content_status import load_content_status, public_status_for_match
from backend.queries import freshness as q_freshness
from backend.queries import league_stats as q_league_stats
from backend.queries import market_cards as q_market_cards
from backend.queries import reco as q_reco
from backend.queries import match_preview as q_preview
from backend.queries import match_report as q_report
from backend.queries import matches as q_matches
from backend.queries import odds as q_odds
from backend.queries.leagues import LEAGUE_META, anonymous_cacheable_league_ids, league_data_profiles

from .deps import NO_STORE, AuthContext, core_ro, get_auth_context, odds_ro, platform_ro
from .schemas import (
    AnalysisBundleDTO,
    CooccurrenceResponse,
    FreshnessResponse,
    LeagueFixturesResponse,
    LeagueInfo,
    LeagueSeasonProfileResponse,
    MatchDetailResponse,
    MatchListResponse,
    MatchMarketCardsResponse,
    MatchOddsResponse,
    MatchPreviewResponse,
    MatchReportResponse,
    PlayersResponse,
    ProductsResponse,
    StandingsResponse,
    TeamStatsResponse,
    error_responses,
)

router = APIRouter(
    prefix="/api/v1",
    tags=["public"],
    responses=error_responses(400, 404, 422),
)

PUBLIC_CACHE = "public, s-maxage=300, stale-while-revalidate=60"
PUBLIC_CACHE_SHORT = "public, s-maxage=60, stale-while-revalidate=30"

# 全部已收录联赛现在对匿名与登录用户返回一致内容(单一真源见 queries/leagues.py)。
# 取代散落的 `league_id == 47` 硬编码——所有联赛都应进公共缓存,不再有需要
# 登录才能访问、因而必须 no-store 的联赛。
ANON_CACHEABLE = anonymous_cacheable_league_ids()


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


def _require_known_league(league_id: int) -> None:
    """联赛必须在 LEAGUE_META 登记才允许访问对应端点;未知联赛 404。

    2026-08-16 产品权限口径修正后,门禁只剩这一步——不再有任何基于
    entitlement/登录状态的 401/403(原 `_require_league_access` 的
    entitlement 分支已删除)。"""
    if league_id not in LEAGUE_META:
        raise HTTPException(status_code=404, detail="未知联赛")


# ── 联赛 ───────────────────────────────────────────────────

@router.get("/leagues", response_model=list[LeagueInfo])
def list_leagues(
    response: Response,
    conn=Depends(core_ro),
):
    # 2026-08-16 起响应内容不随请求者身份变化(entitlement/accessible/
    # requires_login 字段已删除),但仍保守走 no-store——数据来自
    # league_data_profiles/durable_status,与"是否登录"无关,只是尚未纳入
    # 公共缓存 allowlist,不在本次权限口径修正范围内变更。
    response.headers["Cache-Control"] = NO_STORE
    profiles = league_data_profiles(conn)
    durable_status = load_content_status()
    return [
        LeagueInfo(
            league_id=lid,
            code=m["code"],
            name_zh=m["name_zh"],
            name_en=m["name_en"],
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
    # all=总榜 / home=主场 / away=客场 / form=近期 / xg=xG 榜。
    # 后四档共 2,892 行此前 100% 不可见(standings 硬编码 'all')。
    table_type: str = Query("all", pattern="^(all|home|away|form|xg)$"),
    conn=Depends(core_ro),
):
    _require_known_league(league_id)
    response.headers["Cache-Control"] = PUBLIC_CACHE if league_id in ANON_CACHEABLE else NO_STORE
    data = q_matches.standings(conn, league_id, season, table_type=table_type)
    if not data["rows"]:
        data["empty_reason"] = "该联赛暂无积分榜数据"
    return {"league_id": league_id, "table_type": table_type, **data}


@router.get("/leagues/{league_id}/fixtures", response_model=LeagueFixturesResponse)
def league_fixtures(
    league_id: int,
    response: Response,
    season: str | None = None,
    status: str | None = Query(None, pattern="^(upcoming|finished)$"),
    limit: int = Query(100, ge=1, le=400),
    offset: int = Query(0, ge=0),
    conn=Depends(core_ro),
):
    _require_known_league(league_id)
    response.headers["Cache-Control"] = PUBLIC_CACHE if league_id in ANON_CACHEABLE else NO_STORE
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
    conn=Depends(core_ro),
):
    """球队赛季统计(2026-08-16 起全字段免费投影,含角球/红黄牌/零封/BTTS)。"""
    _require_known_league(league_id)
    response.headers["Cache-Control"] = PUBLIC_CACHE if league_id in ANON_CACHEABLE else NO_STORE
    data = q_league_stats.team_season_stats(conn, league_id, season)
    if not data["rows"]:
        data["empty_reason"] = "该联赛暂无球队赛季统计数据"
    return {"league_id": league_id, **data}


@router.get(
    "/leagues/{league_id}/season-profile",
    response_model=LeagueSeasonProfileResponse,
    response_model_exclude_unset=True,
)
def league_season_profile(
    league_id: int,
    response: Response,
    season: str | None = None,
    conn=Depends(core_ro),
):
    """联赛速览:进球时段 / 比分分布 / 大小球阈值 / 主客胜率。

    数据来自四张早已构建但前端零消费的银层表。legacy 的
    /api/league/{id}/betting 曾查过同一批数据,但 §10.1 禁止继续扩展 legacy,
    这里在 v1 新建。
    """
    _require_known_league(league_id)
    response.headers["Cache-Control"] = (
        PUBLIC_CACHE if league_id in ANON_CACHEABLE else NO_STORE
    )
    data = q_league_stats.league_season_profile(conn, league_id, season)
    if data["summary"] is None and not data["goal_minutes"]:
        data["empty_reason"] = "该联赛该赛季暂无赛季统计数据"
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
    conn=Depends(core_ro),
):
    """球员榜(5 维度:进球/助攻/xG/xGOT/评分,各 top 10)。"""
    _require_known_league(league_id)
    response.headers["Cache-Control"] = PUBLIC_CACHE if league_id in ANON_CACHEABLE else NO_STORE
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
    # 向过去的窗口(2026-08-19):yesterday/past3d/past7d 是"赛果"视图用的,
    # 与 today/tomorrow/3d/7d 一一对称(语义见 q_matches._window_bounds)。
    # today 本来就是北京自然日、天然双向,「今天赛果」用 today&status=finished
    # 即可,刻意不另造 token。
    window: str | None = Query(
        None, pattern="^(today|tomorrow|3d|7d|all|yesterday|past3d|past7d)$"
    ),
    content: str | None = Query(None, pattern="^(analysis|odds|shots)$"),
    # 首页重点位确定性选场(2026-08-16):default limit 分页天然只看"前 N 条
    # API 原始顺序",完整候选窗口里更靠后的比赛永远没机会被选中,哪怕它才是
    # 唯一一场"免费且已发布概率"的比赛。opt-in(默认不生效)——不改变其它
    # 调用方已经依赖的默认排序/分页语义,只在显式请求时把"当前调用方能看到
    # 概率的免费比赛"顶进 limit 截断线以内,不下发额外的整窗口数据。
    boost: str | None = Query(None, pattern="^(free_predicted)$"),
    q: str | None = Query(None, min_length=1, max_length=80),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: AuthContext = Depends(get_auth_context),
    conn=Depends(core_ro),
    conn_platform=Depends(platform_ro),
    conn_odds=Depends(odds_ro),
):
    """比赛列表:所有已收录联赛的比赛都出现在列表里,内容对任何人(含匿名)
    完全一致(2026-08-16 起除"每日精选"外全站比赛内容全部免费,登录与内容
    分层彻底解耦)。"""
    # 内容不随身份变化,但请求带 Cookie(已登录)时仍不进共享缓存
    # (CLAUDE.md §10.2 的一般 Cookie 规则,与本场比赛内容是否分层无关)。
    response.headers["Cache-Control"] = NO_STORE if ctx.authenticated else PUBLIC_CACHE_SHORT
    visible_league_ids = set(LEAGUE_META.keys())
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
    #
    # 2026-08-19 性能修复:这两个集合与下面 D8 给每场标 odds_coverage_tier
    # 要用的 full_set/legacy_set 是同一 JOIN 条件的同一条 SQL——原先在这里
    # 和 D8 各自独立查一遍(生产实测重复这一遍 32ms)。提到这里查一次、两处
    # 复用,逻辑不变,只是不再算两遍。
    full_set, legacy_set = q_odds.odds_coverage_sets(conn_odds)
    odds_match_ids = full_set | legacy_set
    # content=shots:双方球队都有历史射门数据 → 赛前射门分布图画得出来。
    # 与 analysis(已发布预测)/odds(有赔率)并列的第三种"这场有东西可看"判据,
    # 且是当前唯一大面积成立的一种(实测未来 7 天 38/77 场,而 analysis 为 0)。
    # 只在真正请求这一档时才算(要扫一遍 dim_match,不放进每请求的公共开销)。
    match_ids = (
        analysis_match_ids
        if content == "analysis"
        else odds_match_ids
        if content == "odds"
        else q_matches.matches_with_shot_history(conn)
        if content == "shots"
        else None
    )
    # 首页/列表页胜平负概率条:同一批次一次查询算出全部比赛,不逐场请求
    # (N+1 会拖垮一页 20 场的列表)。2026-08-16 起恒返回最新快照,不再对
    # 匿名施加 1 小时延迟(与 /matches/{id}/odds 端点同步解除)。
    #
    # boost=free_predicted(2026-08-16 首页重点位确定性选场):在完整候选窗口内
    # (不受 limit 截断)确定性地找出"已有发布概率"的比赛,顶进 ORDER BY 最高
    # 优先级——保证首页重点位不会因为"前 limit 条恰好都没有算出概率"而找不到
    # 真正满足条件的比赛。查询参数名沿用旧值 free_predicted(不改前端契约),
    # 但 2026-08-16 起访问权不再与联赛挂钩,不再需要按联赛过滤。
    #
    # 2026-08-19 性能修复:只有 boost=free_predicted 才需要"完整候选窗口内
    # 确定性地找"这个语义,必须在分页前对全站算一遍(生产实测 104ms)。其余
    # 请求(占绝大多数)只需要给"这一页"的比赛标概率,等 list_matches() 分页
    # 返回后再按这一页的 match_id 收窄查询即可——延后到下面 D8。
    if boost == "free_predicted":
        win_prob_by_match = q_odds.latest_1x2_by_match(conn_odds)
        free_predicted_match_ids: set[int] = set(win_prob_by_match.keys())
    else:
        win_prob_by_match = None  # 分页后按本页 match_id 收窄计算,见下方 D8
        free_predicted_match_ids = set()
    result = q_matches.list_matches(
        conn,
        visible_league_ids,
        date=date,
        status=status,
        league_id=league_id,
        season=season,
        window=window,
        query=q,
        query_team_ids=query_team_ids,
        match_ids=match_ids,
        priority_match_ids=analysis_match_ids | odds_match_ids,
        top_priority_match_ids=free_predicted_match_ids,
        limit=limit,
        offset=offset,
    )
    result["matches"] = [_with_content_status(match) for match in result["matches"]]
    # D8:逐场标注赔率覆盖档位;full_set/legacy_set 已在上面算过一次并复用,
    # 不重新查(2026-08-19 起不再是"每请求批量算一次"的独立第二次调用)。
    #
    # P1.1:coverage_tier 只回答"有没有过数据",这两个字段回答"数据新不新"——
    # 同样每请求批量算一次,不逐场比赛单独查(N+1);2026-08-19 起收窄到本页
    # 真正返回的 match_id(见 odds_last_observed_by_match 的 match_ids 参数),
    # 不再为全站 737K 条快照做 GROUP BY。
    page_match_ids = {match["match_id"] for match in result["matches"]}
    odds_last_observed = q_odds.odds_last_observed_by_match(conn_odds, match_ids=page_match_ids)
    if win_prob_by_match is None:
        win_prob_by_match = q_odds.latest_1x2_by_match(conn_odds, match_ids=page_match_ids)
    for match in result["matches"]:
        mid = match["match_id"]
        match["odds_coverage_tier"] = (
            "full_timeline"
            if mid in full_set
            else "open_close_only"
            if mid in legacy_set
            else "none"
        )
        last_observed = odds_last_observed.get(mid)
        match["odds_last_observed_at"] = last_observed
        match["odds_freshness_state"] = q_odds.classify_odds_freshness(last_observed)
        # 概率条恒下发(算出来才有,没算出来就是 None——与登录/权限无关)。
        match["win_probability"] = win_prob_by_match.get(mid)
    # D4:回显赛季上下文,让 ?season= 的 0 结果可解释。
    if league_id is not None:
        available_seasons = q_matches.seasons_of_league(conn, league_id)
    else:
        seen: set[str] = set()
        for lid in sorted(visible_league_ids):
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
    conn=Depends(core_ro),
    conn_platform=Depends(platform_ro),
    conn_odds=Depends(odds_ro),
):
    m = q_matches.match_by_id(conn, match_id)
    if m is None:
        raise HTTPException(status_code=404, detail="比赛不存在")
    _require_known_league(m["league_id"])
    response.headers["Cache-Control"] = PUBLIC_CACHE_SHORT if m["league_id"] in ANON_CACHEABLE else NO_STORE
    m = _with_content_status(m)
    # 单场端点用单场版查询(2026-08-19 性能修复):odds_coverage_sets()/
    # odds_last_observed_by_match() 是给列表路由"一次算全部、避免 N+1"用的,
    # 这里只需要这一场比赛的结果,不该为它遍历全站 737K 条快照——
    # odds_coverage_for_match/odds_last_observed_for_match 与整表版逐位等价
    # (同一 JOIN 条件、同一 review_status 门槛),见
    # tests/backend/test_odds_single_match_scoped.py。
    full_hit, legacy_hit = q_odds.odds_coverage_for_match(conn_odds, match_id)
    m["odds_coverage_tier"] = (
        "full_timeline" if full_hit
        else "open_close_only" if legacy_hit
        else "none"
    )
    # P1.1:与列表路由同一口径的赔率新鲜度(coverage_tier 只回答"有没有过数据",
    # 这两个字段回答"数据新不新")。
    last_observed = q_odds.odds_last_observed_for_match(conn_odds, match_id)
    m["odds_last_observed_at"] = last_observed
    m["odds_freshness_state"] = q_odds.classify_odds_freshness(last_observed)
    home_form = q_matches.recent_form(conn, m["home"]["team_id"], m["date_utc"])
    away_form = q_matches.recent_form(conn, m["away"]["team_id"], m["date_utc"])
    try:
        reco_published = match_id in q_reco.published_match_ids(conn_platform)
    except sqlite3.OperationalError:
        reco_published = False   # 推荐板块表尚未迁移时如实为 False,不阻塞详情页
    return {
        "match": m,
        "data_updated_at": m.get("data_updated_at"),
        "home_form": home_form,
        "away_form": away_form,
        "reco_published": reco_published,
    }


@router.get("/matches/{match_id}/analysis", response_model=AnalysisBundleDTO)
def match_analysis(
    match_id: int,
    response: Response,
    conn_core=Depends(core_ro),
    conn_platform=Depends(platform_ro),
    conn_odds=Depends(odds_ro),
):
    """公开版 analysis_bundle:恒完整返回(2026-08-16 起除"每日精选"外全站
    比赛内容全部免费,不再按 entitlement 投影;与 Studio 共用生成逻辑)。

    响应内容会随预测发布/赔率刷新变化 → private, no-store。
    """
    from backend.studio.bundle import build_analysis_bundle

    response.headers["Cache-Control"] = NO_STORE
    m = q_matches.match_by_id(conn_core, match_id)
    if m is None:
        raise HTTPException(status_code=404, detail="比赛不存在")
    _require_known_league(m["league_id"])
    bundle = build_analysis_bundle(conn_core, conn_platform, conn_odds, match_id)
    bundle["cooccurrence_count"] = len(bundle["cooccurring_events"])
    bundle.pop("subtitle_cues", None)   # 页面用不到,字幕留给 Studio(与权限无关)
    return bundle


# ── 赔率与同期事件 ─────────────────────────────────────────

@router.get("/matches/{match_id}/odds", response_model=MatchOddsResponse)
def match_odds(
    match_id: int,
    response: Response,
    conn_core=Depends(core_ro),
    conn_odds=Depends(odds_ro),
):
    """恒返回完整快照时间线(2026-08-16 起除"每日精选"外全站比赛内容全部
    免费,不再对匿名/无订阅用户施加 1 小时延迟摘要)。"""
    response.headers["Cache-Control"] = NO_STORE
    m = q_matches.match_by_id(conn_core, match_id)
    if m is None:
        raise HTTPException(status_code=404, detail="比赛不存在")
    _require_known_league(m["league_id"])

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
    if xref is None:
        return _legacy_odds_fallback(conn_odds, match_id)

    rows = conn_odds.execute(
        """SELECT market, company_id, company_name, market_phase, payload_json,
                  source_updated_at, observed_at
           FROM bronze_ng_odds_snap WHERE provider_match_id=?
           ORDER BY market, company_id, observed_at""",
        (xref["provider_match_id"],),
    ).fetchall()
    snapshots = [dict(r) | {"payload": json.loads(r["payload_json"])} for r in rows]
    for s in snapshots:
        s.pop("payload_json", None)
        # 公司显示名读侧归一(见 q_odds.canonical_company_name):同一家公司的
        # 多 company_id + 多拼写(Bet365 id 8/"Bet365" vs id 281/"bet 365";
        # Macauslot id 1 vs 80)在同一页面里以统一品牌名显示,不改存储、不合并行。
        s["company_name"] = q_odds.canonical_company_name(s["company_id"], s.get("company_name"))
    if not snapshots:
        # 完整时间线口径下无快照 → 尝试旧项目两点摘要,再不行才 unavailable
        return _legacy_odds_fallback(
            conn_odds, match_id,
            no_data_reason="已建立映射,但暂无满足口径的赔率快照",
        )
    observation_count = conn_odds.execute(
        """SELECT COUNT(DISTINCT observed_at) FROM bronze_ng_odds_snap
           WHERE provider_match_id=? AND market='1x2' AND market_phase='pre_match'""",
        (xref["provider_match_id"],),
    ).fetchone()[0]
    return {
        "match_id": match_id,
        "available": True,
        "tier": "full",
        "coverage_tier": "full_timeline",
        "home_away_inverted": bool(xref["home_away_inverted"]),
        "observation_count": observation_count,
        "display_mode": "odds_changes" if observation_count >= 2 else "current_odds",
        "snapshots": snapshots,
    }


def _legacy_odds_fallback(conn_odds, match_id: int,
                          no_data_reason: str = "该场比赛暂无已验证的赔率数据映射"):
    """旧项目两点摘要(bronze_legacy_odds_summary)兜底。

    数据已在入库时归一为 canonical 方向(方向修正见 backend/cli/ingest_legacy_odds.py),
    无逐条观测时间戳 → coverage_tier='open_close_only',前端不得画走势图。
    恒给 initial+latest 两点(2026-08-16 起不再按 odds:history_full 投影裁剪)。
    """
    # SQL 收口在 backend/queries/odds.py::legacy_summary_points,
    # 与 studio/bundle 共用——保证两处对同一场比赛看到完全相同的点集。
    rows = q_odds.legacy_summary_points(conn_odds, match_id)
    if not rows:
        return {"match_id": match_id, "available": False, "reason": no_data_reason}
    return {
        "match_id": match_id,
        "available": True,
        "tier": "full",
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
    conn_core=Depends(core_ro),
    conn_odds=Depends(odds_ro),
):
    """同期事件(时间共现,不声称因果)。2026-08-16 起恒含明细,不再区分
    免费(计数)/付费(明细)。"""
    response.headers["Cache-Control"] = NO_STORE
    m = q_matches.match_by_id(conn_core, match_id)
    if m is None:
        raise HTTPException(status_code=404, detail="比赛不存在")
    _require_known_league(m["league_id"])
    total = conn_odds.execute(
        "SELECT COUNT(*) FROM gold_move_cooccurrence WHERE fotmob_match_id=?", (match_id,)
    ).fetchone()[0]
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
    return {
        "match_id": match_id,
        "count": total,
        "items": [dict(r) for r in rows],
        "note": None if total else "暂无同期事件",
    }


@router.get("/matches/{match_id}/report", response_model=MatchReportResponse)
def match_report(
    match_id: int,
    response: Response,
    conn=Depends(core_ro),
):
    """完赛事实报告:阵容/事件/射门/球队与球员统计(详情页四 tab 数据源)。

    门禁:只有"联赛是否已登记"这一条(未知联赛 404),不分付费档位、不区分
    登录状态——本端点全部是已完赛的历史事实,不含模型输出、不含赔率方法论。
    缓存:任何联赛都可进公共缓存(cache_policy PUBLIC_ALLOWLIST 已收录本路径);
    请求带 Cookie 时中间件强制 no-store,登录响应不会污染共享缓存。
    """
    m = q_matches.match_by_id(conn, match_id)
    if m is None:
        raise HTTPException(status_code=404, detail="比赛不存在")
    _require_known_league(m["league_id"])
    response.headers["Cache-Control"] = (
        PUBLIC_CACHE if m["league_id"] in ANON_CACHEABLE else NO_STORE
    )
    data = q_report.match_report(conn, match_id)
    if data is None:
        return {"match_id": match_id, "available": False,
                "reason": "该场比赛暂无逐场事实数据(未完赛,或数据尚未入库)"}
    return {"match_id": match_id, "available": True, **data}


@router.get("/matches/{match_id}/preview", response_model=MatchPreviewResponse)
def match_preview(
    match_id: int,
    response: Response,
    conn_core=Depends(core_ro),
    conn_odds=Depends(odds_ro),
):
    """赛前预览:预计阵容+伤停快照、球队风格象限、进攻来源拆解、关键球员占比、
    门将对位——数据 tab 阵容/风格/球员三个子 tab 的唯一数据源。

    门禁与 /report、/markets 同级:只有"联赛是否已登记"这一条,不分付费
    档位、不区分登录状态。全部由两队各自历史聚合与已采集快照构成,赛前与
    赛后都能给,不含模型输出或赔率方法论。
    """
    m = q_matches.match_by_id(conn_core, match_id)
    if m is None:
        raise HTTPException(status_code=404, detail="比赛不存在")
    _require_known_league(m["league_id"])
    response.headers["Cache-Control"] = (
        PUBLIC_CACHE if m["league_id"] in ANON_CACHEABLE else NO_STORE
    )
    return q_preview.build_match_preview(conn_core, conn_odds, m)


@router.get("/matches/{match_id}/markets", response_model=MatchMarketCardsResponse)
def match_markets(
    match_id: int,
    response: Response,
    conn_core=Depends(core_ro),
    conn_platform=Depends(platform_ro),
    conn_odds=Depends(odds_ro),
):
    """赛前市场卡:两队各自历史均值 → 离线标定表查历史命中率 → 结论
    (data 倾向 + 星级)+ 折叠归因明细。这是赛前之墙唯一能给的"这场比赛
    特有"内容之一(未开赛比赛没有任何赛后事实表数据,只有两队历史聚合)。

    盘口线:goals/corners 在真的抓到 NowGoal 实时盘口时用真实线
    (line_source="market"),没有时退回统计参考线(line_source="statistical");
    yellow_cards 恒为统计参考线(NowGoal 没有罚牌市场)。

    门禁与 /report 同级:只有"联赛是否已登记"这一条,不区分付费档位、不区分
    登录状态——数据倾向是本站对访客建立信任的内容。
    """
    m = q_matches.match_by_id(conn_core, match_id)
    if m is None:
        raise HTTPException(status_code=404, detail="比赛不存在")
    _require_known_league(m["league_id"])
    response.headers["Cache-Control"] = (
        PUBLIC_CACHE if m["league_id"] in ANON_CACHEABLE else NO_STORE
    )
    cards = q_market_cards.match_market_cards(
        conn_core, conn_platform, conn_odds,
        fotmob_match_id=match_id,
        home_id=m["home"]["team_id"], away_id=m["away"]["team_id"],
        league_id=m["league_id"], before_date=m["date_utc"],
    )
    return {"match_id": match_id, "window": q_market_cards.WINDOW, "cards": cards}


# ── 今日更新状态(首页) ────────────────────────────────────

@router.get("/status/freshness", response_model=FreshnessResponse)
def status_freshness(
    response: Response,
    conn_platform=Depends(platform_ro),
    conn_odds=Depends(odds_ro),
):
    """首页「今日更新状态」聚合:赛程/赔率/推荐三条最近成功时间戳,各自附带
    FRESH/STALE/UNAVAILABLE 三态(classify_freshness),供前端判断"这个数据源
    现在是不是正常"而不只是看到一个孤立的钟点数字。

    只读聚合,不承载任何比赛/推荐内容,匿名公开缓存安全(短 TTL,
    因为三个来源各自独立轮询,新鲜度本身随时在变)。
    """
    response.headers["Cache-Control"] = PUBLIC_CACHE_SHORT
    schedule_updated_at = q_freshness.latest_schedule_sync(conn_odds)
    odds_updated_at = q_freshness.latest_odds_observation(conn_odds)
    reco_updated_at = q_freshness.latest_reco_publish(conn_platform)
    return {
        "schedule_updated_at": schedule_updated_at,
        "odds_updated_at": odds_updated_at,
        "reco_updated_at": reco_updated_at,
        "schedule_state": q_freshness.classify_freshness(schedule_updated_at),
        "odds_state": q_freshness.classify_freshness(odds_updated_at),
        "reco_state": q_freshness.classify_freshness(reco_updated_at),
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
