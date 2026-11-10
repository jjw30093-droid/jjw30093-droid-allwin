"""Bounded snapshot evidence and match-scoped person references; no raw IDs exported."""
import json
from datetime import datetime
from backend.vendor.miaodi_standard.contract import stable_id,number,utc,source_id
from backend.vendor.miaodi_standard.lineup_context import lineup_context
from backend.vendor.miaodi_standard.facts import player_identity_status
from backend.queries.presentation_text import display_name
from backend.queries.league_stats import _player_i18n_map
from backend.media.match_portraits import valid_portrait_source


def reference(mid,kind,value,location):
    try:
        if isinstance(value,bool) or not isinstance(value,(str,int)):raise ValueError('invalid_reference')
        key=source_id(value)
    except ValueError:key='unresolved:'+location
    return stable_id('prematch_reference','miaodi',mid+'/'+kind+'/'+key)


def issue(code,field,record=None):return {'code':code,'field':field,**({'record_id':record} if record else {})}


def optional_time(value):
    try:return utc(value) if value is not None else None
    except ValueError:return None


def snapshot(c,sql,args):
    row=c.execute(sql,args).fetchone()
    if row is None:return {'availability':'not_collected','observed_at':None,'payload':None,'snapshot_key':None}
    state={'availability':'invalid','observed_at':optional_time(row['observed_at']),'payload':None,'snapshot_key':str(row['id'])}
    raw=row['payload_json']
    if not isinstance(raw,str) or len(raw)>1024*1024:return state
    try:payload=json.loads(raw)
    except (TypeError,ValueError):return state
    if isinstance(payload,dict):state.update(availability='observed',payload=payload)
    return state


def person(ids,lookup,mid,raw,location,i18n=None):
    source=raw.get('id',raw.get('player_id'));record=reference(mid,'player',source,location)
    try:
        if isinstance(source,bool) or not isinstance(source,(str,int)):raise ValueError('invalid_reference')
        normalized=source_id(source)
    except ValueError:normalized=None
    pid=lookup('player',normalized) if normalized is not None else None;quality=[]
    if pid is None:quality.append(issue('unresolved_identity','player_id',record))
    elif player_identity_status(ids,'fotmob',source)=='reference_only':quality.append(issue('identity_reference_only','player_id',record))
    zh=(i18n or {}).get(str(source),(None,None));name=display_name(zh[1] or zh[0] or raw.get('name'),source,'姓名未收录')
    numeric=valid_portrait_source(source)
    avatar=(f'/api/v1/media/player-avatars/{pid}.png' if pid else f'/api/v1/media/prematch-portraits/{mid}/{record}.png') if numeric else None
    return {'record_id':record,'player_id':pid,'name':name,'avatar_url':avatar,'issues':quality}


def roster_evidence(ids,core,odds,mid,match,lookup):
    i18n=_player_i18n_map(core);sid=match['match_id'];s=snapshot(odds,'''SELECT id,payload_json,observed_at FROM bronze_fm_lineup_snap
        WHERE fotmob_match_id=? ORDER BY observed_at DESC,id DESC LIMIT 1''',(sid,));p=s['payload'] or {};quality=[]
    context=lineup_context('fotmob',p.get('lineup_type') if isinstance(p.get('lineup_type'),str) else None,observed_at=s['observed_at'],source=p.get('source') if isinstance(p.get('source'),str) else None)
    phase='unknown'
    kickoff=optional_time(match.get('kickoff_at_utc'))
    if s['observed_at'] and kickoff:
        phase='before_kickoff' if datetime.fromisoformat(s['observed_at'].replace('Z','+00:00'))<datetime.fromisoformat(kickoff.replace('Z','+00:00')) else 'at_or_after_kickoff'
    result={'availability':s['availability'],'snapshot_id':stable_id('snapshot','miaodi',mid+'/lineup/'+s['snapshot_key']) if s['snapshot_key'] else None,
            'context':context,'capture_phase':phase,'home':None,'away':None}
    if s['availability']=='invalid':quality.append(issue('invalid_snapshot','lineups'))
    for side in ('home','away'):
        raw=p.get(side)
        if raw is None:continue
        if not isinstance(raw,dict):quality.append(issue('invalid_snapshot','lineups'));continue
        target={'team_id':lookup('team',match[side]['team_id']),'formation':raw.get('formation') if isinstance(raw.get('formation'),str) else None,'coach':None,'starters':[],'subs':[],'bench_availability':'unknown'}
        for section in ('starters','subs'):
            players=raw.get(section) or []
            if not isinstance(players,list):quality.append(issue('invalid_snapshot','lineups'));continue
            if len(players)>100:raise ValueError('roster_player_limit')
            for index,r in enumerate(players):
                if not isinstance(r,dict):r={};quality.append(issue('invalid_snapshot','lineups'))
                row=person(ids,lookup,mid,r,f'lineup/{side}/{section}/{index}',i18n)
                row['shirt_number']=str(r['shirt_number']) if isinstance(r.get('shirt_number'),(str,int)) and not isinstance(r.get('shirt_number'),bool) else None
                for key in ('pos_x','pos_y'):
                    try:row[key]=number(r.get(key),low=0,high=1)
                    except ValueError:row[key]=None;row['issues'].append(issue('invalid_source_value',key,row['record_id']))
                target[section].append(row)
        if target['subs']:target['bench_availability']='observed'
        elif p.get('source')=='enetpulse':target['bench_availability']='not_supplied_by_prediction_feed'
        coach=raw.get('coach')
        if isinstance(coach,dict):
            ref=reference(mid,'coach',coach.get('id'),side+'/coach')
            target['coach']={'reference_id':ref,'coach_id':None,'identity_status':'snapshot_reference',
                'name':display_name(coach.get('name'),coach.get('id'),'教练姓名未收录'),
                'avatar_url':f'/api/v1/media/prematch-portraits/{mid}/{ref}.png' if valid_portrait_source(coach.get('id')) else None}
        result[side]=target
    sidelined={}
    for side in ('home','away'):
        capture=snapshot(odds,'''SELECT id,payload_json,observed_at FROM bronze_fm_sideline_snap
            WHERE fotmob_match_id=? AND fotmob_team_id=? ORDER BY observed_at DESC,id DESC LIMIT 1''',(sid,match[side]['team_id']))
        players=(capture['payload'] or {}).get('sidelined')
        if not isinstance(players,list):
            if capture['availability']=='observed':capture['availability']='invalid'
            players=[]
        if len(players)>100:raise ValueError('sidelined_player_limit')
        rows=[]
        for index,r in enumerate(players):
            if not isinstance(r,dict):capture['availability']='invalid';continue
            row=person(ids,lookup,mid,r,f'sidelined/{side}/{index}',i18n)
            row.update(reason=r.get('reason') if isinstance(r.get('reason'),str) else None,
                expected_return=r.get('expected_return') if isinstance(r.get('expected_return'),str) else None);rows.append(row)
        sidelined[side]={'availability':capture['availability'],'observed_at':capture['observed_at'],'source_updated_at':None,'players':rows}
        if capture['availability']=='invalid':quality.append(issue('invalid_snapshot','sidelined'))
    return result,sidelined,quality


def portrait_source(odds,mid,match,record):
    """Resolve only observed IDs in the current bounded snapshots, never user URLs."""
    candidates=[];sid=match['match_id']
    r=snapshot(odds,'''SELECT id,payload_json,observed_at FROM bronze_fm_lineup_snap WHERE fotmob_match_id=? ORDER BY observed_at DESC,id DESC LIMIT 1''',(sid,))
    p=r['payload'] or {}
    for side in ('home','away'):
        raw=p.get(side)
        if isinstance(raw,dict):
            coach=raw.get('coach')
            if isinstance(coach,dict):candidates.append(('coach',coach.get('id'),side+'/coach'))
            for section in ('starters','subs'):
                players=raw.get(section) or []
                if not isinstance(players,list) or len(players)>100:continue
                candidates.extend(('player',v.get('id'),f'lineup/{side}/{section}/{i}') for i,v in enumerate(players) if isinstance(v,dict))
        s=snapshot(odds,'''SELECT id,payload_json,observed_at FROM bronze_fm_sideline_snap WHERE fotmob_match_id=? AND fotmob_team_id=? ORDER BY observed_at DESC,id DESC LIMIT 1''',(sid,match[side]['team_id']))
        players=(s['payload'] or {}).get('sidelined',[])
        if isinstance(players,list) and len(players)<=100:candidates.extend(('player',v.get('id'),f'sidelined/{side}/{i}') for i,v in enumerate(players) if isinstance(v,dict))
    found={valid_portrait_source(value) for kind,value,location in candidates if reference(mid,kind,value,location)==record and valid_portrait_source(value)}
    if len(found)>1:raise ValueError('ambiguous_person_portrait')
    return next(iter(found)) if found else None
