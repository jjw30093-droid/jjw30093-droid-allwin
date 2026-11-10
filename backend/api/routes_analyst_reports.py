"""Administrator-only analyst reports, edits, jobs and immutable exports."""
import json
import os
from pathlib import Path
from typing import Literal
from uuid import UUID
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .deps import require_admin, require_csrf, platform_ro, platform_rw, NO_STORE
from backend.studio import analyst_workflow as flow, research
from backend.db.connections import tx
from backend.db.paths import data_dir
from backend.db.util import new_uuid, utc_now_iso


def enabled():
    if os.environ.get('STUDIO_REPORT_WORKFLOW_ENABLED')!='1':raise HTTPException(404,'报告工作流尚未启用')


def private(response:Response):response.headers['Cache-Control']=NO_STORE


router=APIRouter(prefix='/api/v1/studio/reports',tags=['analyst-reports'],dependencies=[Depends(require_admin),Depends(enabled),Depends(private)])


class Generate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    match_id:UUID
    phase:Literal['prematch','postmatch']
    window:Literal[5,10]=5
    since:date|None=None
    venue:Literal['all','home','away']='all'
    state:Literal['all','drawing','leading','trailing']='all'
    eleven:Literal[0,1]=1
    opponent:UUID|None=None
    pair_a:UUID|None=None
    pair_b:UUID|None=None

    @model_validator(mode='after')
    def valid_pair(self):
        if bool(self.pair_a)!=bool(self.pair_b) or (self.pair_a and self.pair_a==self.pair_b):
            raise ValueError('请选择两个不同球员')
        if self.phase=='postmatch' and (self.since or self.venue!='all' or self.state!='all' or self.opponent):
            raise ValueError('赛后快照展示本场完整过程，历史筛选仅用于赛前')
        return self


class JudgmentEdit(BaseModel):
    model_config=ConfigDict(extra='forbid')
    text:str=Field(default='',max_length=3000)
    video_note:str=Field(default='',max_length=3000)
    withdrawn:bool=False
    reason:str=Field(default='',max_length=1000)

    @model_validator(mode='after')
    def withdrawal_reason(self):
        if self.withdrawn and not self.reason.strip():raise ValueError('撤回判断必须注明原因')
        return self


class Edit(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision:int=Field(ge=1)
    editorial_notes:str=Field(max_length=20000)
    judgments:dict[str,JudgmentEdit]=Field(default_factory=dict,max_length=5)


class Review(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision:int=Field(ge=1)
    status:Literal['draft','reviewed']


class Export(BaseModel):
    kind:Literal['json','pdf']
    expected_revision:int=Field(ge=1)


@router.get('/candidates')
def candidates():
    try:return flow.source_read('candidates')
    except (OSError,ValueError):raise HTTPException(503,'候选比赛来源暂不可用') from None


@router.get('')
def listing(ctx=Depends(require_admin),conn=Depends(platform_ro)):
    rows=conn.execute('''SELECT d.id,d.title,d.status,a.match_id,a.phase,a.captured_at,r.revision
        FROM analyst_report_links a JOIN content_drafts d ON d.id=a.draft_id
        JOIN research_draft_links r ON r.draft_id=d.id WHERE d.user_id=?
        ORDER BY a.captured_at DESC,a.rowid DESC LIMIT 200''',(ctx.user_id,)).fetchall()
    return {'reports':[dict(r) for r in rows]}


@router.get('/jobs')
def jobs(ctx=Depends(require_admin),conn=Depends(platform_ro)):
    return {'jobs':[dict(r) for r in conn.execute("SELECT id,status,attempt,error_summary,meta_json,created_at FROM job_runs WHERE job_name='analyst_report' AND json_extract(meta_json,'$.user_id')=? ORDER BY created_at DESC LIMIT 100",(ctx.user_id,))]}


@router.post('/generate',dependencies=[Depends(require_csrf)])
def generate(body:Generate,ctx=Depends(require_admin),conn=Depends(platform_rw)):
    # Explicit generation may rerun a failed input; scheduler uses source fingerprints.
    params=body.model_dump(mode='json',exclude={'match_id','phase'},exclude_none=True)
    return {'job_id':flow.enqueue(conn,ctx.user_id,str(body.match_id),body.phase,new_uuid(),params)}


def read_owned(conn,ctx,did):
    try:return flow.load(conn,ctx.user_id,str(did))
    except LookupError:raise HTTPException(404,'报告不存在') from None
    except ValueError:raise HTTPException(409,'报告证据校验失败') from None


@router.get('/{draft_id}')
def detail(draft_id:UUID,ctx=Depends(require_admin),conn=Depends(platform_ro)):
    return read_owned(conn,ctx,draft_id)


@router.get('/{draft_id}/history')
def history(draft_id:UUID,ctx=Depends(require_admin),conn=Depends(platform_ro)):
    read_owned(conn,ctx,draft_id)
    return {'events':[dict(r) for r in conn.execute('SELECT revision,action,content_json,created_at FROM research_draft_events WHERE draft_id=? ORDER BY revision DESC LIMIT 100',(str(draft_id),))]}


@router.get('/{draft_id}/compare/{other_id}')
def compare(draft_id:UUID,other_id:UUID,ctx=Depends(require_admin),conn=Depends(platform_ro)):
    current=read_owned(conn,ctx,draft_id);other=read_owned(conn,ctx,other_id)
    a,b=other['report'],current['report']
    if a['match']['id']!=b['match']['id'] or a['phase']!=b['phase']:raise HTTPException(422,'只能比较同场同阶段版本')
    return {'before':{'id':other['id'],'cutoff_at':a['cutoff_at'],'revision':other['revision']},
        'after':{'id':current['id'],'cutoff_at':b['cutoff_at'],'revision':current['revision']},
        'lineup':flow.roster_diff(a.get('lineup'),b.get('lineup')),
        'filters_changed':a.get('filters')!=b.get('filters'),
        'teams':[{'name':t['name'],'before':{k:a['teams'][i].get(k) for k in ('attack','defence','minutes')},
                  'after':{k:t.get(k) for k in ('attack','defence','minutes')},
                  'before_samples':[s['id'] for s in a['teams'][i]['samples']],
                  'after_samples':[s['id'] for s in t['samples']]} for i,t in enumerate(b['teams'])]}


@router.post('/{draft_id}/edit',dependencies=[Depends(require_csrf)])
def edit(draft_id:UUID,body:Edit,ctx=Depends(require_admin),conn=Depends(platform_rw)):
    value=read_owned(conn,ctx,draft_id)
    if set(body.judgments)-{h['id'] for h in value['report']['hypotheses']}:raise HTTPException(422,'判断编号不属于本报告')
    try:research.change(conn,str(draft_id),ctx.user_id,body.expected_revision,overrides={'editorial_notes':body.editorial_notes,'judgments':{k:v.model_dump() for k,v in body.judgments.items()}})
    except ValueError as e:raise HTTPException(409,str(e)) from None
    return read_owned(conn,ctx,draft_id)


@router.post('/{draft_id}/review',dependencies=[Depends(require_csrf)])
def review(draft_id:UUID,body:Review,ctx=Depends(require_admin),conn=Depends(platform_rw)):
    read_owned(conn,ctx,draft_id)
    try:research.change(conn,str(draft_id),ctx.user_id,body.expected_revision,status=body.status)
    except ValueError as e:raise HTTPException(409,str(e)) from None
    return read_owned(conn,ctx,draft_id)


@router.post('/{draft_id}/export',dependencies=[Depends(require_csrf)])
def export(draft_id:UUID,body:Export,ctx=Depends(require_admin),conn=Depends(platform_rw)):
    value=read_owned(conn,ctx,draft_id)
    if value['revision']!=body.expected_revision:raise HTTPException(409,'版本已变化，请重新读取后导出')
    job=new_uuid();folder=data_dir()/'exports';folder.mkdir(parents=True,exist_ok=True)
    target=folder/(job+'.'+body.kind)
    if body.kind=='json':target.write_bytes(research.canonical(value))
    else:
        from backend.studio.analyst_pdf import render
        try:render(value,target)
        except ImportError:raise HTTPException(503,'PDF 运行依赖尚未安装') from None
        except ValueError:raise HTTPException(503,'PDF 中文字体未配置或报告内容无效') from None
    with tx(conn):
        conn.execute("INSERT INTO export_jobs(id,user_id,draft_id,kind,status,file_path,meta_json,created_at,finished_at) VALUES (?,?,?,?,'succeeded',?,?,?,?)",
            (job,ctx.user_id,str(draft_id),body.kind,str(target),json.dumps({'profile':flow.VERSION,'revision':value['revision'],'review':value['review']}),utc_now_iso(),utc_now_iso()))
    return {'job_id':job,'download_url':f'/api/v1/studio/exports/{job}/download'}
