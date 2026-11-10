"""Analysis rights use the shared platform account, not Studio roles or VIP plans."""
import os
import json
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from backend.auth.analysis_access import AccessConflict, change_membership, resolve_access
from backend.db.connections import tx
from .deps import AuthContext, NO_STORE, platform_ro, platform_rw, require_admin, require_user
from .routes_admin import require_admin_csrf
from .schemas import error_responses

router = APIRouter(prefix='/api/v1', tags=['analysis-access'],
                   responses=error_responses(401, 403, 409, 422, 503))


class AnalysisAccessDTO(BaseModel):
    product: Literal['analysis']
    allowed: bool
    basis: Literal['none', 'account_disabled', 'membership', 'trial']
    checked_at: str
    trial_ends_at: str | None
    membership_ends_at: str | None
    membership_revision: int
    membership_status: Literal['active', 'revoked'] | None


class MembershipDTO(BaseModel):
    user_id: str
    status: Literal['active', 'revoked']
    starts_at: str
    ends_at: str
    revision: int
    updated_at: str


class MembershipBody(BaseModel):
    action: Literal['grant', 'revoke']
    ends_at: str | None = None
    expected_revision: int = Field(ge=0, strict=True)
    request_key: str = Field(min_length=8, max_length=128)
    reason: str = Field(min_length=1, max_length=500)


def _enabled(response):
    response.headers['Cache-Control'] = NO_STORE
    if os.environ.get('ANALYSIS_ACCESS_ENABLED') != '1':
        raise HTTPException(503, detail='分析会员服务尚未开放')


def _read(conn, user_id):
    try:
        return resolve_access(conn, user_id)
    except sqlite3.Error:
        raise HTTPException(503, detail='权益服务暂不可用') from None


def require_analysis_reader(ctx: AuthContext = Depends(require_user), conn=Depends(platform_ro)):
    if os.environ.get('ANALYSIS_ACCESS_ENABLED') != '1':
        raise HTTPException(503, detail='分析会员服务尚未开放')
    if ctx.role in ('admin', 'analyst'):
        return ctx
    if not _read(conn, ctx.user_id)['allowed']:
        raise HTTPException(403, detail='分析试用已到期，请通过公众号联系开通会员')
    return ctx


class MembershipEventDTO(BaseModel):
    id: str
    user_id: str
    actor_id: str
    revision: int
    action: Literal['grant', 'revoke']
    reason: str
    created_at: str
    before: MembershipDTO | None
    after: MembershipDTO


class MembershipHistoryDTO(BaseModel):
    events: list[MembershipEventDTO]


@router.get('/me/analysis-access', response_model=AnalysisAccessDTO)
def my_access(response: Response, ctx: AuthContext = Depends(require_user), conn=Depends(platform_ro)):
    _enabled(response)
    return _read(conn, ctx.user_id)


@router.get('/admin/users/{user_id}/analysis-access', response_model=AnalysisAccessDTO)
def user_access(user_id: str, response: Response, ctx: AuthContext = Depends(require_admin), conn=Depends(platform_ro)):
    _enabled(response)
    return _read(conn, user_id)


@router.get('/admin/users/{user_id}/analysis-access/history', response_model=MembershipHistoryDTO)
def access_history(user_id: str, response: Response, ctx: AuthContext = Depends(require_admin), conn=Depends(platform_ro)):
    _enabled(response)
    try:
        rows = conn.execute('''SELECT id,user_id,actor_id,revision,action,reason,created_at,before_json,after_json
            FROM analysis_membership_events WHERE user_id=? ORDER BY revision DESC LIMIT 100''', (user_id,)).fetchall()
        return {'events': [{**{k: r[k] for k in ('id','user_id','actor_id','revision','action','reason','created_at')},
                            'before': json.loads(r['before_json']), 'after': json.loads(r['after_json'])} for r in rows]}
    except (sqlite3.Error, ValueError):
        raise HTTPException(503, detail='权益历史暂不可用') from None


@router.post('/admin/users/{user_id}/analysis-access', response_model=MembershipDTO)
def update_access(user_id: str, body: MembershipBody, response: Response,
                  ctx: AuthContext = Depends(require_admin_csrf), conn=Depends(platform_rw)):
    _enabled(response)
    try:
        with tx(conn):
            return change_membership(conn, actor_id=ctx.user_id, user_id=user_id, **body.model_dump())
    except AccessConflict:
        raise HTTPException(409, detail='权益版本或请求编号冲突，请刷新后重试') from None
    except PermissionError:
        raise HTTPException(403, detail='需要有效管理员权限') from None
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(422, detail='请核对账号、原因和带时区的到期时间') from None
    except sqlite3.Error:
        raise HTTPException(503, detail='权益服务暂不可用') from None
