"""Public article projections and isolated editorial publication commands."""
import os
import sqlite3
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from backend.auth.analysis_access import resolve_access
from backend.editorial import publications
from .deps import AuthContext, NO_STORE, get_auth_context, platform_ro, platform_rw
from .routes_studio import require_analyst, require_analyst_csrf
from .schemas import error_responses

router=APIRouter(prefix='/api/v1',tags=['analysis'],responses=error_responses(401,403,404,409,422,503))


class ArticleSummary(BaseModel):
    slug: str
    publication_version: int
    title: str
    excerpt: str
    kind: Literal['prematch','postmatch','round']
    visibility: Literal['public','member']
    published_at: str


class ArticleList(BaseModel):
    articles: list[ArticleSummary]


class PublishedMetric(BaseModel):
    label: str | None
    value: float | None
    unit: str | None
    side: str | None
    definition_version: str | None


class PublishedBody(BaseModel):
    body: str
    data_cutoff_at: str
    analysis_mode: Literal['current','reconstructed']
    teams: dict[str,str]
    metrics: list[PublishedMetric]
    coverage: dict[str, int | bool] | None = None


class ArticleDetail(ArticleSummary):
    content: PublishedBody


class PublicationState(BaseModel):
    slug: str | None
    publication_version: int
    published: bool


class PublicationCommand(BaseModel):
    expected_revision: int=Field(ge=1,strict=True)
    expected_publication_version: int=Field(ge=0,strict=True)
    slug: str=Field(min_length=1,max_length=100,pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
    excerpt: str=Field(min_length=1,max_length=300)
    body: str=Field(min_length=1,max_length=20000)
    kind: Literal['prematch','postmatch','round']='postmatch'
    visibility: Literal['public','member']
    reason: str=Field(min_length=1,max_length=500)
    confirmed: Literal[True]


class RetractCommand(BaseModel):
    expected_publication_version: int=Field(ge=1,strict=True)
    reason: str=Field(min_length=1,max_length=500)


def _run(fn):
    try: return fn()
    except PermissionError: raise HTTPException(403,detail='需要有效编辑权限') from None
    except LookupError: raise HTTPException(404,detail='内容不存在') from None
    except publications.PublicationConflict: raise HTTPException(409,detail='版本已变化或内容校验失败，请重新载入') from None
    except ValueError: raise HTTPException(422,detail='请检查发布内容、审核状态与栏目') from None
    except sqlite3.Error: raise HTTPException(503,detail='内容服务暂不可用') from None


@router.get('/analysis/articles',response_model=ArticleList)
def articles(response:Response,conn=Depends(platform_ro)):
    response.headers['Cache-Control']=NO_STORE
    return _run(lambda:{'articles':publications.list_articles(conn)})


@router.get('/analysis/articles/{slug}',response_model=ArticleDetail)
def article(slug:str,response:Response,ctx:AuthContext=Depends(get_auth_context),conn=Depends(platform_ro)):
    response.headers['Cache-Control']=NO_STORE
    def read():
        # Keep publication pointer and membership judgment in one read snapshot.
        conn.execute('BEGIN')
        try:
            item,content=publications.read_article(conn,slug)
            if item['visibility']=='member':
                if not ctx.authenticated: raise HTTPException(401,detail='登录后查看分析')
                if ctx.role not in ('admin','analyst'):
                    if os.environ.get('ANALYSIS_ACCESS_ENABLED')!='1':
                        raise HTTPException(503,detail='分析会员尚未开放')
                    if not resolve_access(conn,ctx.user_id)['allowed']:
                        raise HTTPException(403,detail='分析试用已到期，请通过公众号联系开通')
            return {**item,'content':content}
        finally: conn.rollback()
    return _run(read)


@router.get('/studio/research/drafts/{draft_id}/publication',response_model=PublicationState)
def publication_state(draft_id:str,ctx:AuthContext=Depends(require_analyst),conn=Depends(platform_ro)):
    return _run(lambda:publications.state(conn,draft_id,ctx.user_id))


@router.post('/studio/research/drafts/{draft_id}/publication',response_model=PublicationState)
def publish(draft_id:str,body:PublicationCommand,ctx:AuthContext=Depends(require_analyst_csrf),conn=Depends(platform_rw)):
    return _run(lambda:publications.publish(conn,draft_id=draft_id,actor_id=ctx.user_id,**body.model_dump(exclude={'confirmed'})))


@router.post('/studio/research/drafts/{draft_id}/retract',response_model=PublicationState)
def retract(draft_id:str,body:RetractCommand,ctx:AuthContext=Depends(require_analyst_csrf),conn=Depends(platform_rw)):
    return _run(lambda:publications.retract(conn,draft_id=draft_id,actor_id=ctx.user_id,**body.model_dump()))
