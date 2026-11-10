"""Private administrator research visualizations; no source query parameters."""
from datetime import date
from uuid import UUID
from fastapi import APIRouter,Depends,HTTPException,Query,Response
from .deps import require_admin,NO_STORE
from .standard_identity import configured_store, source_team_id
from backend.media.team_crests import resolve_team_crest_url
from backend.studio.visualization import read,Catalog,Bundle,VisualizationError

router=APIRouter(prefix='/api/v1/studio/visualization',tags=['studio-visualization'],dependencies=[Depends(require_admin)])

def project(response,path,params,model):
    response.headers['Cache-Control']=NO_STORE
    try:return read(path,params,model)
    except VisualizationError as e:
        message={404:'比赛尚未接入可视化',409:'比赛数据已更新，请重新加载整场数据',503:'比赛数据暂不可用，请稍后重试'}[e.status]
        raise HTTPException(e.status,message,headers={'Cache-Control':NO_STORE}) from None

@router.get('/matches',response_model=Catalog)
def matches(response:Response,date:date|None=None,competition:str=Query('',pattern='^[A-Z0-9_]{0,12}$'),
            q:str=Query('',max_length=80),offset:int=Query(0,ge=0,le=10000),limit:int=Query(20,ge=1,le=50)):
    params={'offset':offset,'limit':limit,'competition':competition,'q':q}
    if date:params['date']=date.isoformat()
    return project(response,'matches',params,Catalog)

def team_crest(identity: str, store: str) -> str | None:
    try:
        return resolve_team_crest_url('fotmob', source_team_id(UUID(identity), store))
    except (ValueError, HTTPException):
        return None

@router.get('/matches/{match_id}',response_model=Bundle)
def match(match_id:UUID,response:Response,offset:int=Query(0,ge=0,le=10000),limit:int=Query(500,ge=1,le=1000),
          version:str|None=Query(None,pattern='^[a-f0-9]{64}$')):
    params={'offset':offset,'limit':limit}
    if version:params['version']=version
    result=project(response,'matches/'+str(match_id),params,Bundle)
    if result.match.id!=str(match_id) or result.offset!=offset or (version and version!=result.version):
        raise HTTPException(503,'比赛数据校验失败',headers={'Cache-Control':NO_STORE})
    store = configured_store()
    if store:
        result.match.home_crest_url = team_crest(result.match.home_id, store)
        result.match.away_crest_url = team_crest(result.match.away_id, store)
    return result
