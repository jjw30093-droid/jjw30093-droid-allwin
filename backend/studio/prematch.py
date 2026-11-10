"""Private CC analyst report transport and cutoff-aware VIP lineup context."""
import json
import os
from hashlib import sha256
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import build_opener, ProxyHandler
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from backend.studio.predictions import _NoRedirect
from backend.db.connections import connect_ro
from backend.api.standard_identity import configured_store, source_match_id, source_team_id
from uuid import UUID

class PrematchError(RuntimeError): pass

def analyst_source_base():
    """A separately budgeted loopback tunnel can isolate analyst reads."""
    base=os.environ.get('STUDIO_ANALYST_SOURCE_BASE','http://127.0.0.1:18768').rstrip('/')
    u=urlsplit(base)
    if u.scheme!='http' or u.hostname not in ('127.0.0.1','localhost','::1') or u.username or u.password or u.path or u.query or u.fragment or not u.port:
        raise ValueError('分析来源必须是本机内部HTTP隧道')
    return base

from backend.vendor.miaodi_standard.roster_link import match_roster_profile

class Report(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    schema_version: Literal['prematch-studio.v1']
    definition_version: str
    match: dict
    cutoff_at: str
    generated_at: str
    historical_as_known_proven: bool
    filters: dict
    teams: list[dict]=Field(max_length=2,min_length=2)
    modules: list[dict]=Field(max_length=8,min_length=8)
    findings: list[dict]=Field(max_length=5)
    references: list[dict]
    lineup: dict
    notes: list[str]
    revision: str=Field(pattern='^[a-f0-9]{64}$')
    snapshot_observed_at: str | None = None
    competition_context: dict | None = None

def read_report(path, params):
    try:
        with build_opener(ProxyHandler({}),_NoRedirect()).open(
            analyst_source_base()+'/v1/prematch/'+path+'?'+urlencode(params),timeout=90 if str(params.get('context'))=='1' else 25) as response:
            if response.status!=200 or response.headers.get_content_type()!='application/json': raise ValueError('bad_response')
            raw=response.read(20_000_001)
        if len(raw)>20_000_000: raise ValueError('report_too_large')
        value=json.loads(raw)
        return Report.model_validate(value).model_dump() if path.startswith('matches/') else value
    except (OSError,HTTPError,HTTPException,ValueError,TypeError):
        raise PrematchError('赛前数据暂不可用') from None

def resolve_lineup_match(report,store):
    """Reuse mapped teams and exact kickoff when a match adoption is not yet published."""
    from fastapi import HTTPException as APIError
    from backend.api.standard_identity import _read
    from contextlib import closing
    try:
        return source_match_id(UUID(report['match']['id']),store),{'method':'published_match_identity'}
    except APIError as exc:
        if exc.status_code!=404:raise
    home=source_team_id(UUID(report['match']['home_id']),store)
    away=source_team_id(UUID(report['match']['away_id']),store)
    with closing(connect_ro('core')) as core:
        matches=core.execute('''SELECT Match_ID FROM dim_match WHERE League_ID=47
            AND Home_Team_ID=? AND Away_Team_ID=? AND kickoff_precision='exact'
            AND kickoff_source IS NOT NULL AND julianday(kickoff_at_utc)=julianday(?) LIMIT 2''',
            (home,away,report['match']['kickoff_at'])).fetchall()
    if len(matches)!=1:raise ValueError('lineup_match_missing_or_ambiguous')
    with _read(store) as registry:
        row=registry.execute("SELECT entity_id FROM source_ids WHERE kind='match' AND provider='fotmob' AND source_id=?",(str(matches[0][0]),)).fetchone()
    if not row:raise ValueError('lineup_match_unpublished')
    mid=source_match_id(UUID(row[0]),store)
    if mid!=matches[0][0]:raise ValueError('lineup_match_identity_conflict')
    return mid,{'method':'exact_kickoff_and_published_team_ids','lineup_match_id':row[0],
                'evidence_match_id':report['match']['id'],'kickoff_at':report['match']['kickoff_at']}

def lineup_position(item):
    """Retain the provider's schematic placement; never infer tracking positions."""
    from math import isfinite
    x,y=item.get('pos_x'),item.get('pos_y')
    valid=all(isinstance(v,(int,float)) and not isinstance(v,bool) and isfinite(v) and 0<=v<=1 for v in (x,y))
    return {'pos_x':x if valid else None,'pos_y':y if valid else None,
            'coordinate_frame':'provider_vertical_0_1' if valid else None,
            'shirt_number':item.get('shirt_number')}


def lineup_context(report):
    """Read projected/confirmed XIs and injuries independently at one cutoff."""
    import sqlite3
    from contextlib import closing
    from fastapi import HTTPException as APIError
    from backend.api.standard_identity import _read
    store=configured_store()
    if not store:return report
    context={'status':'unavailable','players':[],'teams':[],'injuries':[]}
    try:
        mid,link=resolve_lineup_match(report,store)
        context['identity_link']=link
        with closing(connect_ro('core')) as core:
            match=core.execute('SELECT Home_Team_ID,Away_Team_ID FROM dim_match WHERE Match_ID=?',(mid,)).fetchone()
        if not match:return report
        with closing(connect_ro('odds')) as c:
            c.execute('BEGIN')
            cutoff=report['cutoff_at']
            row=c.execute("""SELECT id,payload_json,payload_hash,observed_at,lineup_type FROM bronze_fm_lineup_snap
                WHERE fotmob_match_id=? AND julianday(observed_at)<=julianday(?) AND julianday(ingested_at)<=julianday(?)
                ORDER BY julianday(observed_at) DESC,id DESC LIMIT 1""",(mid,cutoff,cutoff)).fetchone()
            if row:
                payload=json.loads(row['payload_json'])
                if not isinstance(payload,dict):raise ValueError('invalid_lineup')
                with _read(store) as registry:
                    for index,side in enumerate(('home','away')):
                        raw=payload.get(side) or {}
                        if raw.get('team_id') is not None and str(raw['team_id'])!=str(match[index]):raise ValueError('lineup_team_mismatch')
                        groups={}
                        for section,limit in (('starters',11),('subs',30)):
                            people=[]
                            for item in (raw.get(section) or [])[:limit]:
                                if not isinstance(item,dict):continue
                                mapped=registry.execute("SELECT entity_id FROM source_ids WHERE kind='player' AND provider='fotmob' AND source_id=?",(str(item.get('id')),)).fetchone()
                                pid=mapped[0] if mapped else None
                                profile,receipt=match_roster_profile(item,report['teams'][index]['players'],pid)
                                if profile is None and pid and receipt.get('method')=='unresolved':
                                    receipt={'method':'published_player_identity_without_recent_history','evidence_player_id':pid}
                                people.append({'id':pid,'name':item.get('name') or '未提供姓名','profile':profile,'identity_link':receipt,**lineup_position(item)})
                            groups[section]=people
                        from collections import Counter
                        linked=Counter((p.get('identity_link') or {}).get('evidence_player_id') for p in groups['starters']+groups['subs'])
                        for person in groups['starters']+groups['subs']:
                            evidence=(person.get('identity_link') or {}).get('evidence_player_id')
                            if evidence and linked[evidence]>1:
                                person.update(profile=None,identity_link={'method':'ambiguous_roster_collision'})
                            person['identity_status']='matched' if (person.get('identity_link') or {}).get('evidence_player_id') else 'unresolved'
                            person['history_status']='available' if person.get('profile') else 'no_recent_sample' if person['identity_status']=='matched' else 'identity_unresolved'
                        context['teams'].append({'side':side,'formation':raw.get('formation'),'players':groups['starters'],'subs':groups['subs']})
                context.update(status='available',snapshot_id=str(row['id']),observed_at=row['observed_at'],type=row['lineup_type'] or payload.get('lineup_type'))
            for index,side in enumerate(('home','away')):
                injury=c.execute("""SELECT id,payload_json,observed_at FROM bronze_fm_sideline_snap WHERE fotmob_match_id=? AND fotmob_team_id=?
                    AND julianday(observed_at)<=julianday(?) AND julianday(ingested_at)<=julianday(?)
                    ORDER BY julianday(observed_at) DESC,id DESC LIMIT 1""",(mid,match[index],cutoff,cutoff)).fetchone()
                entry={'side':side,'status':'unavailable','players':[]}
                if injury:
                    data=json.loads(injury['payload_json']); source=data.get('sidelined',[]) if isinstance(data,dict) else data
                    if not isinstance(source,list):raise ValueError('invalid_injuries')
                    entry.update(status='available',snapshot_id=str(injury['id']),observed_at=injury['observed_at'],
                        players=[{'name':p.get('name'),'reason':p.get('reason'),'expected_return':p.get('expected_return')} for p in source[:100] if isinstance(p,dict)])
                context['injuries'].append(entry)
        report['lineup']=context
        extra=[]
        for index,t in enumerate(context['teams']):
            profiles=[p['profile'] for p in t['players'] if p['profile']]
            known=[p['xa'] for p in profiles if p['xa'] is not None]
            if profiles:
                xa_text=f'{sum(known):.2f}' if known else '未提供'
                extra.append({'title':report['teams'][index]['name']+'本场首发的近期样本',
                    'text':f"名单中 {len(profiles)}/{len(t['players'])} 人能够关联所选历史样本；整场口径已记录 xA {xa_text}，覆盖 {len(known)} 人。替补可展开各自历史表现。",
                    'event_refs':[]})
        report['findings']=(extra+report['findings'])[:5]
        report['revision']=sha256(json.dumps([report['revision'],context],sort_keys=True).encode()).hexdigest()
    except (sqlite3.Error,OSError,ValueError,TypeError,KeyError,APIError):
        report['lineup']={'status':'unavailable','players':[],'injuries':[]}
    return report

def snapshot_path(root,mid,snapshot_id):
    import re
    from pathlib import Path
    UUID(mid)
    if not re.fullmatch('[a-f0-9]{64}',snapshot_id):raise ValueError('invalid_snapshot_id')
    folder=Path(root)/mid
    if folder.is_symlink():raise ValueError('invalid_snapshot_directory')
    path=folder/(snapshot_id+'.json')
    if path.is_symlink():raise ValueError('invalid_snapshot_file')
    return path

def read_snapshot(root,mid,snapshot_id):
    with snapshot_path(root,mid,snapshot_id).open('rb') as f:raw=f.read(20_000_001)
    if len(raw)>20_000_000:raise ValueError('oversized_snapshot')
    value=Report.model_validate(json.loads(raw)).model_dump()
    if value['match']['id']!=mid:raise ValueError('snapshot_match_conflict')
    return value

def list_snapshots(root,mid):
    from pathlib import Path
    UUID(mid);folder=Path(root)/mid
    if not folder.exists():return []
    if folder.is_symlink():raise ValueError('invalid_snapshot_directory')
    result=[]
    for path in sorted(folder.glob('*.metadata.json'),key=lambda p:p.stat().st_mtime_ns,reverse=True)[:50]:
        if path.is_symlink() or path.stat().st_size>4096:raise ValueError('invalid_snapshot_metadata')
        value=json.loads(path.read_text())
        snapshot_path(root,mid,value['snapshot_id'])
        result.append({k:value.get(k) for k in ('snapshot_id','observed_at','cutoff_at','lineup_type','window')})
    return result
