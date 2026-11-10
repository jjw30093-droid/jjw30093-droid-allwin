"""Website statistics from the shared standard reader, behind website access rules.

The /api/v1 prefix intentionally shares the existing session cookie scope. The
response schema is the same versioned standard contract as the customer API.
"""
import logging
import sqlite3
import threading
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from backend.db.paths import db_path
from backend.queries import matches as q_matches
from backend.queries.standard_match_report import standard_match_report, standard_match_report_v2
from backend.queries.leagues import anonymous_cacheable_league_ids
from backend.vendor.miaodi_standard.api_contract import PlayerStatistics, TeamStatistics
from backend.vendor.miaodi_standard.facts import team_statistics
from backend.vendor.miaodi_standard.records import player_statistics

from .cache_policy import NO_STORE, PUBLIC_CACHE
from .data_access import data_access_ctx, require_league_access
from .deps import AuthContext, core_ro
from .standard_identity import configured_store, source_match_id, source_competition_id
from backend.queries.standard_player_season import competition_catalog, player_season_view
from .standard_player_season_schema import StandardCompetition, StandardPlayersResponse, StandardPlayerQuadrantResponse
from backend.queries.standard_team_season import team_season_view
from .standard_team_season_schema import StandardStandingsResponse,StandardTeamStatsResponse
from .schemas import error_responses,RecencyWindow,ProfileWindowN
from typing import Literal
from backend.queries.standard_match_preview import standard_preview
from .standard_preview_schema import StandardPreviewResponse,StandardDataProfileResponse
from .standard_report_schema import StandardReportResponse, StandardReportAvailable, StandardReportUnavailable
from .standard_report_identity_schema import StandardReportResponseV2, StandardReportAvailableV2, StandardReportUnavailableV2

router=APIRouter(prefix='/api/v1/standard',tags=['standard'],
                 responses=error_responses(400,401,404,422,429,503))
log=logging.getLogger('allwin.api.standard')
_read_slots=threading.BoundedSemaphore(2)
_readers={'team-statistics':team_statistics,'player-statistics':player_statistics,'report':standard_match_report,'report-v2':standard_match_report_v2}


def _statistics(kind,identity,request,response,conn,ctx):
    if request.query_params:
        raise HTTPException(400,'不支持的查询参数')
    store=configured_store()
    if not store:
        raise HTTPException(503,'标准数据服务暂不可用')
    source_id=source_match_id(identity,store)
    match=q_matches.match_by_id(conn,source_id)
    if match is None:
        raise HTTPException(404,'比赛不存在')
    try:
        require_league_access(ctx,match['league_id'],match)
    except HTTPException as exc:
        # Keep the existing login rule/context without publishing a provider's
        # competition number through this standard route's error payload.
        if exc.status_code==401 and isinstance(exc.detail,dict):
            raise HTTPException(exc.status_code,{k:v for k,v in exc.detail.items() if k!='league_id'},headers=exc.headers) from None
        raise
    if not _read_slots.acquire(blocking=False):
        raise HTTPException(503,'统计读取繁忙，请稍后重试',headers={'Retry-After':'1'})
    try:
        body=_readers[kind](store,db_path('core'),'fotmob',str(identity))
        model=(StandardReportAvailable if body['available'] else StandardReportUnavailable) if kind=='report' else (TeamStatistics if kind=='team-statistics' else PlayerStatistics)
        if kind=='report-v2':model=StandardReportAvailableV2 if body['available'] else StandardReportUnavailableV2
        body=model.model_validate(body)
    except (sqlite3.Error,OSError,ValueError,LookupError,TypeError):
        log.exception('standard %s projection unavailable',kind)
        raise HTTPException(503,'标准数据服务暂不可用') from None
    finally:
        _read_slots.release()
    response.headers['Cache-Control']=PUBLIC_CACHE if match['league_id'] in anonymous_cacheable_league_ids() else NO_STORE
    return body


@router.get('/matches/{identity}/team-statistics',response_model=TeamStatistics,response_model_exclude_unset=True)
def standard_team_statistics(identity:UUID,request:Request,response:Response,
                             conn=Depends(core_ro),ctx:AuthContext=Depends(data_access_ctx)):
    return _statistics('team-statistics',identity,request,response,conn,ctx)


@router.get('/matches/{identity}/player-statistics',response_model=PlayerStatistics,response_model_exclude_unset=True)
def standard_player_statistics(identity:UUID,request:Request,response:Response,
                               conn=Depends(core_ro),ctx:AuthContext=Depends(data_access_ctx)):
    return _statistics('player-statistics',identity,request,response,conn,ctx)


@router.get('/matches/{identity}/report',response_model=StandardReportResponse,response_model_exclude_unset=True)
def standard_report(identity:UUID,request:Request,response:Response,
                    conn=Depends(core_ro),ctx:AuthContext=Depends(data_access_ctx)):
    return _statistics('report',identity,request,response,conn,ctx)


@router.get('/matches/{identity}/report-v2',response_model=StandardReportResponseV2,response_model_exclude_unset=True)
def standard_report_v2(identity:UUID,request:Request,response:Response,
                       conn=Depends(core_ro),ctx:AuthContext=Depends(data_access_ctx)):
    return _statistics('report-v2',identity,request,response,conn,ctx)


@router.get('/competitions', response_model=list[StandardCompetition])
def standard_competitions(request: Request, response: Response, ctx: AuthContext = Depends(data_access_ctx)):
    if request.query_params:
        raise HTTPException(400, '不支持的查询参数')
    store = configured_store()
    if not store:
        raise HTTPException(503, '标准数据服务暂不可用')
    try:
        result = [StandardCompetition.model_validate(row) for row in competition_catalog(store, db_path('core'))]
    except (sqlite3.Error, OSError, ValueError, LookupError, TypeError):
        log.exception('standard competition catalog unavailable')
        raise HTTPException(503, '标准数据服务暂不可用') from None
    response.headers['Cache-Control'] = PUBLIC_CACHE
    return result


def _player_season(kind, identity, season, request, response, ctx, **filters):
    allowed = {'season'} | ({'table_type'} if kind=='standings' else {'recency','venue'} if kind=='team-stats' else set())
    if any(key not in allowed or len(request.query_params.getlist(key))>1 for key in request.query_params):
        raise HTTPException(400, '不支持的查询参数')
    store = configured_store()
    if not store:
        raise HTTPException(503, '标准数据服务暂不可用')
    league_id = source_competition_id(identity, store)
    try:
        require_league_access(ctx, league_id)
    except HTTPException as exc:
        if exc.status_code == 401 and isinstance(exc.detail, dict):
            raise HTTPException(401, {k: v for k, v in exc.detail.items() if k != 'league_id'}, headers=exc.headers) from None
        raise
    if not _read_slots.acquire(blocking=False):
        raise HTTPException(503, '统计读取繁忙，请稍后重试', headers={'Retry-After': '1'})
    try:
        reader = team_season_view if kind in ('standings','team-stats') else player_season_view
        body = reader(store, db_path('core'), str(identity), league_id, season, kind, **filters)
        model = {'players':StandardPlayersResponse,'player-quadrant':StandardPlayerQuadrantResponse,
                 'standings':StandardStandingsResponse,'team-stats':StandardTeamStatsResponse}[kind]
        result = model.model_validate(body)
    except (sqlite3.Error, OSError, ValueError, LookupError, TypeError):
        log.exception('standard player season projection unavailable')
        raise HTTPException(503, '标准数据服务暂不可用') from None
    finally:
        _read_slots.release()
    response.headers['Cache-Control'] = PUBLIC_CACHE if league_id in anonymous_cacheable_league_ids() else NO_STORE
    return result


@router.get('/competitions/{identity}/players', response_model=StandardPlayersResponse)
def standard_players(identity: UUID, request: Request, response: Response,
                     season: str | None = Query(None, pattern=r'^\d{4}(/\d{4})?$'),
                     ctx: AuthContext = Depends(data_access_ctx)):
    return _player_season('players', identity, season, request, response, ctx)


@router.get('/competitions/{identity}/player-quadrant', response_model=StandardPlayerQuadrantResponse)
def standard_player_quadrant(identity: UUID, request: Request, response: Response,
                            season: str | None = Query(None, pattern=r'^\d{4}(/\d{4})?$'),
                            ctx: AuthContext = Depends(data_access_ctx)):
    return _player_season('player-quadrant', identity, season, request, response, ctx)


@router.get('/competitions/{identity}/standings',response_model=StandardStandingsResponse)
def standard_standings(identity:UUID,request:Request,response:Response,
                       season:str|None=Query(None,pattern=r'^\d{4}(/\d{4})?$'),
                       table_type:Literal['all','home','away','form','xg']='all',
                       ctx:AuthContext=Depends(data_access_ctx)):
    return _player_season('standings',identity,season,request,response,ctx,table_type=table_type)


@router.get('/competitions/{identity}/team-stats',response_model=StandardTeamStatsResponse)
def standard_team_season(identity:UUID,request:Request,response:Response,
                         season:str|None=Query(None,pattern=r'^\d{4}(/\d{4})?$'),
                         recency:RecencyWindow|None=None,venue:Literal['all','home','away']='all',
                         ctx:AuthContext=Depends(data_access_ctx)):
    return _player_season('team-stats',identity,season,request,response,ctx,recency=recency,venue=venue)


def _preview(kind,identity,request,response,conn,ctx,**options):
    allowed={'n','venue'} if kind=='data-profile' else set()
    if any(k not in allowed or len(request.query_params.getlist(k))>1 for k in request.query_params):
        raise HTTPException(400,'不支持的查询参数')
    store=configured_store()
    if not store:raise HTTPException(503,'标准数据服务暂不可用')
    sid=source_match_id(identity,store);match=q_matches.match_by_id(conn,sid)
    if match is None:raise HTTPException(404,'比赛不存在')
    try:require_league_access(ctx,match['league_id'],match)
    except HTTPException as exc:
        if exc.status_code==401 and isinstance(exc.detail,dict):
            raise HTTPException(401,{k:v for k,v in exc.detail.items() if k!='league_id'},headers=exc.headers) from None
        raise
    if not _read_slots.acquire(blocking=False):raise HTTPException(503,'统计读取繁忙，请稍后重试',headers={'Retry-After':'1'})
    try:body=standard_preview(store,db_path('core'),db_path('odds'),str(identity),profile_only=kind=='data-profile',**options)
    except (sqlite3.Error,OSError,ValueError,LookupError,TypeError):
        log.exception('standard preview unavailable');raise HTTPException(503,'标准数据服务暂不可用') from None
    finally:_read_slots.release()
    response.headers['Cache-Control']=PUBLIC_CACHE if match['league_id'] in anonymous_cacheable_league_ids() else NO_STORE
    return body


@router.get('/matches/{identity}/preview',response_model=StandardPreviewResponse)
def standard_match_preview(identity:UUID,request:Request,response:Response,conn=Depends(core_ro),ctx:AuthContext=Depends(data_access_ctx)):
    return _preview('preview',identity,request,response,conn,ctx)


@router.get('/matches/{identity}/data-profile',response_model=StandardDataProfileResponse)
def standard_match_profile(identity:UUID,request:Request,response:Response,
                           venue:Literal['recent','same_venue','all']='recent',n:ProfileWindowN=ProfileWindowN.N10,
                           conn=Depends(core_ro),ctx:AuthContext=Depends(data_access_ctx)):
    return _preview('data-profile',identity,request,response,conn,ctx,max_n=int(n),venue_mode=venue)
