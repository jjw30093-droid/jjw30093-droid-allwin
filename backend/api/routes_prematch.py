"""Administrator-only analyst report. The public API performs no source writes."""
import os
from datetime import date
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from .deps import require_admin, NO_STORE
from backend.studio.prematch import read_report, lineup_context, PrematchError, Report, read_snapshot, list_snapshots

def enabled():
    if os.environ.get('STUDIO_PREMATCH_ENABLED','0')!='1':raise HTTPException(404,'赛前分析尚未启用',headers={'Cache-Control':NO_STORE})

router=APIRouter(prefix='/api/v1/studio/prematch',tags=['studio-prematch'],dependencies=[Depends(require_admin),Depends(enabled)])

def project(path,params,response):
    response.headers['Cache-Control']=NO_STORE
    if os.environ.get('STUDIO_PREMATCH_ENABLED','0')!='1':raise HTTPException(404,'赛前分析尚未启用',headers={'Cache-Control':NO_STORE})
    try:return read_report(path,params)
    except PrematchError:raise HTTPException(503,'赛前数据暂不可用',headers={'Cache-Control':NO_STORE}) from None

@router.get('/fixtures')
def fixtures(response:Response):return project('fixtures',{},response)

@router.get('/matches/{match_id}/snapshots')
def snapshots(match_id:UUID,response:Response):
    response.headers['Cache-Control']=NO_STORE
    root=os.environ.get('STUDIO_PREMATCH_SNAPSHOT_DIR')
    if not root:return {'snapshots':[]}
    try:return {'snapshots':list_snapshots(root,str(match_id))}
    except (OSError,ValueError,KeyError):raise HTTPException(503,'历史快照暂不可用',headers={'Cache-Control':NO_STORE}) from None

@router.get('/matches/{match_id}/snapshots/{snapshot_id}',response_model=Report)
def historical(match_id:UUID,snapshot_id:str,response:Response):
    response.headers['Cache-Control']=NO_STORE
    root=os.environ.get('STUDIO_PREMATCH_SNAPSHOT_DIR')
    if not root:raise HTTPException(404,'尚无历史快照',headers={'Cache-Control':NO_STORE})
    try:return read_snapshot(root,str(match_id),snapshot_id)
    except FileNotFoundError:raise HTTPException(404,'快照不存在',headers={'Cache-Control':NO_STORE}) from None
    except (OSError,ValueError,KeyError):raise HTTPException(503,'历史快照暂不可用',headers={'Cache-Control':NO_STORE}) from None

@router.get('/matches/{match_id}',response_model=Report)
def report(match_id:UUID,response:Response,window:int=Query(5,ge=5,le=10),since:date|None=None,
           venue:str=Query('all',pattern='^(all|home|away)$'),state:str=Query('all',pattern='^(all|drawing|leading|trailing)$'),
           eleven:int=Query(1,ge=0,le=1),pair_a:UUID|None=None,pair_b:UUID|None=None,opponent:UUID|None=None):
    if window not in (5,10) or bool(pair_a)!=bool(pair_b) or (pair_a and pair_a==pair_b):
        raise HTTPException(422,'筛选条件无效',headers={'Cache-Control':NO_STORE})
    params={'window':window,'venue':venue,'state':state,'eleven':eleven}
    for key,value in [('since',since),('pair_a',pair_a),('pair_b',pair_b),('opponent',opponent)]:
        if value is not None:params[key]=str(value)
    data=project('matches/'+str(match_id),params,response)
    if data['match']['id']!=str(match_id):raise HTTPException(503,'比赛身份校验失败',headers={'Cache-Control':NO_STORE})
    from .standard_identity import configured_store
    from .routes_visualization import team_crest
    store=configured_store()
    if store:
        for side in ('home','away'):
            data['match'][side+'_crest_url']=team_crest(data['match'][side+'_id'],store)
    return lineup_context(data)
