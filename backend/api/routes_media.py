"""Read-only, manifest-bound same-origin media routes."""

import sqlite3
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, Request

from backend.media.team_crests import read_team_crest, read_team_crest_asset
from backend.media.match_portraits import valid_portrait_source

from .schemas import error_responses
from .standard_identity import configured_store, source_player_id, source_match_id
from .deps import core_ro,odds_ro

router = APIRouter(
    prefix="/api/v1/media",
    tags=["media"],
    responses=error_responses(404, 422),
)


def _crest_response(crest):
    if crest is None:
        raise HTTPException(status_code=404, detail="队徽不存在")
    return Response(content=crest.content, media_type="image/png", headers={
        "ETag": f'"{crest.sha256}"',
        "Cache-Control": "public, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
    })


@router.get('/team-crests/{digest}.png', response_class=Response,
            responses={**error_responses(400), 200: {'description': 'Manifest-verified content-addressed crest', 'content': {'image/png': {}}}})
def team_crest_asset(request: Request, digest: str = Path(..., pattern=r'^[0-9a-f]{64}$')):
    if request.query_params:
        raise HTTPException(400, '不支持的查询参数')
    return _crest_response(read_team_crest_asset(digest))


@router.get(
    "/team-crests/{provider}/{provider_team_id}.png",
    response_class=Response,
    responses={
        200: {
            "description": "Manifest-bound PNG team crest",
            "content": {"image/png": {}},
        }
    },
)
def team_crest(
    provider: str,
    provider_team_id: int,
    v: str = Query(..., min_length=12, max_length=12),
):
    crest = read_team_crest(provider, provider_team_id, version=v)
    return _crest_response(crest)


def _portrait_response(source: int):
    return Response(status_code=200, headers={
        'X-Accel-Redirect': f'/_miaodi_player_avatar/{source}.png',
        'Cache-Control': 'private, no-store',
        'X-Content-Type-Options': 'nosniff',
    })


@router.get('/match-portraits/{identity}/referee.png', response_class=Response,
            responses={**error_responses(400, 503), 200: {'description': 'Match-scoped official portrait through the internal media cache', 'content': {'image/png': {}}}})
def match_referee_portrait(identity: UUID, request: Request, conn=Depends(core_ro)):
    if request.query_params:
        raise HTTPException(400, '不支持的查询参数')
    store = configured_store()
    if not store:
        raise HTTPException(503, '图片编号服务暂不可用')
    match_id = source_match_id(identity, store)
    try:
        row = conn.execute('SELECT Referee_ID FROM dim_match WHERE Match_ID=?', (match_id,)).fetchone()
    except sqlite3.Error:
        raise HTTPException(503, '图片信息暂不可用') from None
    source = valid_portrait_source(row[0]) if row else None
    if source is None:
        raise HTTPException(404, '裁判图片不存在')
    return _portrait_response(source)


@router.get('/player-avatars/{identity}.png', response_class=Response,
            responses={**error_responses(400, 503), 200: {'description': 'Same-origin player image through the internal media cache', 'content': {'image/png': {}}}})
def player_avatar(identity: UUID, request: Request):
    if request.query_params:
        raise HTTPException(400, '不支持的查询参数')
    store = configured_store()
    if not store:
        raise HTTPException(503, '图片编号服务暂不可用')
    source = source_player_id(identity, store)
    # Nginx consumes and removes this internal header. No browser redirect and
    # no network download in the Python serving process. The internal location
    # has a fixed upstream, separate bounded disk cache and no forwarded cookies.
    return _portrait_response(source)


@router.get('/prematch-portraits/{identity}/{record}.png',response_class=Response,
            responses={**error_responses(400,503),200:{'description':'Observed match-scoped person portrait','content':{'image/png':{}}}})
def prematch_person_portrait(identity:UUID,record:UUID,request:Request,core=Depends(core_ro),odds=Depends(odds_ro)):
    if request.query_params:raise HTTPException(400,'不支持的查询参数')
    store=configured_store()
    if not store:raise HTTPException(503,'图片编号服务暂不可用')
    sid=source_match_id(identity,store)
    from backend.queries.matches import match_by_id
    from backend.queries.standard_preview_people import portrait_source
    match=match_by_id(core,sid)
    if match is None:raise HTTPException(404,'图片不存在')
    try:source=portrait_source(odds,str(identity),match,str(record))
    except (sqlite3.Error,ValueError,TypeError):raise HTTPException(503,'图片信息暂不可用') from None
    if source is None:raise HTTPException(404,'图片不存在')
    return _portrait_response(source)
