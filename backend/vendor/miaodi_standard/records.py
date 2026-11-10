"""Standard event/shot/player observations, preserving sparse source facts.

No deduplication by minute/coordinates; missing source identities use a clearly
snapshot-scoped record identity rather than a made-up stable event identity.
"""
import collections
import json
from contextlib import closing
from .contract import number,stable_id,utc
from .codec import digest
from .facts import read,canonical_id,match_source,flag,snapshots,identity_lookup
from .metrics import observation,definition_status
from .identity_lookup import resolve

PERIODS={1:'first_half',2:'second_half',3:'extra_time_first_half',4:'extra_time_second_half',5:'penalty_shootout',
 'FirstHalf':'first_half','SecondHalf':'second_half','ExtraFirstHalf':'extra_time_first_half','ExtraSecondHalf':'extra_time_second_half',
 'ExtraTimeFirstHalf':'extra_time_first_half','ExtraTimeSecondHalf':'extra_time_second_half','PenaltyShootout':'penalty_shootout'}
VIP_EVENT_KINDS={'Goal':'goal','Card':'card','Substitution':'substitution','AddedTime':'added_time','Half':'period_marker','Comment':'comment','Var':'video_review','VAR':'video_review'}
CC_EVENT_KINDS={'goal_event':'goal','own_goal_event':'own_goal','card_event':'card','structural_tombstone_candidate':'structural_correction_candidate','starting_formation_marker':'initial_formation','formation_change_marker':'formation_change','substitution_off':'substitution_off','substitution_on':'substitution_on','var_decision_event':'video_review_decision','var_reviewed_event':'video_review','assist_action':'assist_action','key_pass_action':'key_pass','pass_candidate':'pass_candidate','unresolved_provider_event':'unknown'}


def safe_number(value,issues,field,kind='number',low=None,high=None):
    try:return number(value,kind,low,high)
    except ValueError:
        issues.append({'field':field,'code':'invalid_source_value'});return None


def location(x,y,provider,issues):
    length,width=(105,68) if provider=='fotmob' else (100,100)
    x=safe_number(x,issues,'x');y=safe_number(y,issues,'y')
    # Event locations may lie beyond a touchline or goal line. Preserve finite
    # coordinates and flag them; do not silently clamp or erase valid evidence.
    for field,value,limit in [('x',x,length),('y',y,width)]:
        if value is not None and not 0<=value<=limit:issues.append({'field':field,'code':'outside_pitch_bounds'})
    return {'x':x*100/length if x is not None else None,'y':y*100/width if y is not None else None,
            'unit':'percent_of_pitch','frame':'team_attacking','physical_direction':None}


def clock(minute,second,period,added,issues,elapsed_ms=None,non_playing=False):
    phase='non_playing' if non_playing else PERIODS.get(period)
    if phase is None and period is not None:issues.append({'field':'period','code':'unmapped_period'})
    return {'minute':safe_number(minute,issues,'minute','count',0),'second':safe_number(second,issues,'second','count',0,59),
            'added_minutes':safe_number(added,issues,'added_minutes','count',0),'period':phase,
            'elapsed_ms':safe_number(elapsed_ms,issues,'elapsed_ms','count',0)}


def record_identity(provider,kind,sid,source_event_id,row,seen):
    if source_event_id not in (None,'',0,'0',-1,'-1'):
        identity=stable_id(kind,provider,str(sid)+'/'+str(source_event_id))
        return identity,'source_event',identity
    key=digest(row);occurrence=seen[key];seen[key]+=1
    return stable_id(kind,provider,str(sid)+'/'+key[:40]+'/'+str(occurrence)),'snapshot_record',None


def envelope(match_id,provider,kind,items,issues):
    return {'schema_version':'miaodi-'+kind+'.v1','match_id':match_id,
            'series_id':stable_id('series',provider,kind+'-v1'),
            'observations':items,'quality':{'issues':issues}}


def shots(registry,source,provider,match_id):
    result=[];issues=[];seen=collections.Counter()
    with closing(read(registry)) as ids,closing(read(source)) as data:
        match_id=resolve(ids,match_id)
        sid=match_source(ids,match_id,provider)
        if provider=='fotmob':rows=data.execute('SELECT * FROM fact_shotmap WHERE Match_ID=? ORDER BY rowid LIMIT 1001',(sid,)).fetchall()
        elif provider=='opta':
            rows=data.execute('''SELECT s.*,n.match_elapsed_ms,c.is_goal,c.is_own_goal,c.is_penalty
              FROM op_shot_xg s LEFT JOIN op_event_normalized n ON n.match_id=s.match_id AND n.event_id=s.event_id
              LEFT JOIN op_shot_context c ON c.match_id=s.match_id AND c.event_id=s.event_id
              WHERE s.match_id=? ORDER BY s.event_id LIMIT 1001''',(sid,)).fetchall()
        else:raise ValueError('unsupported_provider')
        if len(rows)>1000:raise ValueError('shot_limit')
        for raw in rows:
            r=dict(raw);vip=provider=='fotmob';record,basis,event=record_identity(provider,'shot',sid,r.get('Shot_ID' if vip else 'event_id'),r,seen)
            item_issues=[];team=canonical_id(ids,'team',provider,r['Team_ID' if vip else 'team_id']);player=canonical_id(ids,'player',provider,r['Player_ID' if vip else 'player_id'])
            outcome={'Goal':'goal','AttemptSaved':'saved','Miss':'miss','Post':'woodwork'}.get(r.get('Outcome')) if vip else ('goal' if r.get('is_goal')==1 else None)
            body={'record_id':record,'event_id':event,'identity_basis':basis,'team_id':team,'player_id':player,
              'clock':clock(r['Minute' if vip else 'minute'],None if vip else r['second'],r['Period' if vip else 'period_id'],r.get('Minute_Added') if vip else None,item_issues,r.get('match_elapsed_ms')),
              'location':location(r['X_Coord' if vip else 'x'],r['Y_Coord' if vip else 'y'],provider,item_issues),
              'expected_goals':safe_number(r['xG' if vip else 'xg'],item_issues,'expected_goals',low=0,high=1),
              'expected_goals_on_target':safe_number(r['xGOT' if vip else 'xgot'],item_issues,'expected_goals_on_target',low=0,high=1),
              'outcome':outcome,'is_own_goal':flag(r.get('Is_Own_Goal' if vip else 'is_own_goal')),
              'is_blocked':flag(r.get('Is_Blocked')) if vip else None,
              'is_on_target':flag(r.get('Is_On_Target')) if vip else None,
              'is_penalty':flag(r.get('is_penalty')) if not vip else (r.get('Situation')=='Penalty' if r.get('Situation') else None)}
            if team is None:item_issues.append({'field':'team_id','code':'unresolved_identity'})
            if player is None:item_issues.append({'field':'player_id','code':'unresolved_identity'})
            if event is None:item_issues.append({'field':'event_id','code':'source_event_id_missing'})
            result.append(body);issues.extend({'record_id':record,**i} for i in item_issues)
    return envelope(match_id,provider,'shots',result,issues)


def events(registry,source,provider,match_id):
    result=[];issues=[];seen=collections.Counter()
    with closing(read(registry)) as ids,closing(read(source)) as data:
        match_id=resolve(ids,match_id)
        sid=match_source(ids,match_id,provider)
        if provider=='fotmob':
            rows=data.execute('SELECT * FROM fact_match_events WHERE Match_ID=? ORDER BY event_index LIMIT 5001',(sid,)).fetchall()
            m=data.execute('SELECT Home_Team_ID,Away_Team_ID FROM dim_match WHERE Match_ID=?',(sid,)).fetchone()
        elif provider=='opta':rows=data.execute('SELECT * FROM op_event_normalized WHERE match_id=? ORDER BY event_order,event_id LIMIT 5001',(sid,)).fetchall()
        else:raise ValueError('unsupported_provider')
        if len(rows)>5000:raise ValueError('event_limit')
        source_counts=collections.Counter(str(r['event_id']) for r in rows if r['event_id'] is not None)
        for raw in rows:
            r=dict(raw);vip=provider=='fotmob';item_issues=[]
            source_event=r.get('event_id')
            if source_event is not None and source_counts[str(source_event)]>1:
                source_event=None;item_issues.append({'field':'event_id','code':'ambiguous_source_event_id'})
            record,basis,eid=record_identity(provider,'timeline_event',sid,source_event,r,seen)
            team=(m[0] if r['is_home']==1 else m[1] if r['is_home']==0 else None) if vip else r['team_id']
            kind=(VIP_EVENT_KINDS.get(r['event_type'],'unknown') if vip else CC_EVENT_KINDS.get(r['event_kind'],'unknown'))
            if kind=='unknown':item_issues.append({'field':'event_kind','code':'unmapped_event_kind'})
            body={'record_id':record,'event_id':eid,'identity_basis':basis,'event_kind':kind,
                  'team_id':canonical_id(ids,'team',provider,team),'player_id':canonical_id(ids,'player',provider,r.get('player_id')),
                  'clock':clock(r['minute' if vip else 'clock_minute'],None if vip else r['clock_second'],None if vip else r['period_id'],r.get('minutes_added') if vip else None,item_issues,r.get('match_elapsed_ms'),r.get('time_status')=='non_playing_period'),
                  'location':None if vip or r.get('coordinate_status')=='not_applicable_non_playing_period' else location(r['x_attacking'],r['y_attacking'],provider,item_issues),
                  'end_location':None if vip or r.get('coordinate_status')=='not_applicable_non_playing_period' else location(r['end_x_attacking'],r['end_y_attacking'],provider,item_issues),
                  'related_players':{},'card':None,'is_structural_correction_candidate':False if vip else flag(r.get('is_structural_tombstone_candidate'))}
            if vip:
                body['related_players']={out:canonical_id(ids,'player',provider,r.get(key)) for out,key in [('assist','assist_player_id'),('substitution_in','sub_in_player_id'),('substitution_out','sub_out_player_id')]}
                body['card']={'Yellow':'yellow','Red':'red','YellowRed':'second_yellow'}.get(r.get('card_type'))
            result.append(body);issues.extend({'record_id':record,**i} for i in item_issues)
    return envelope(match_id,provider,'events',result,issues)


def player_statistics(registry,source,provider,match_id,*,_connections=None,_source_refs=None):
    result=[];issues=[];unmapped=set();seen=set()
    with snapshots(registry,source,_connections) as (ids,data):
        match_id=resolve(ids,match_id)
        identity=identity_lookup(ids,provider)
        sid=match_source(ids,match_id,provider)
        if provider=='fotmob':
            rows=data.execute('SELECT * FROM fact_player_match_stats WHERE Match_ID=? ORDER BY Player_ID LIMIT 201',(sid,)).fetchall()
            if len(rows)>200:raise ValueError('player_count_limit')
            metadata={'Match_ID','Player_ID','Player_Opta_ID','Team_ID','is_goalkeeper','is_captain','shirt_number','position_id','usual_position','player_name'}
            values=[(r['Team_ID'],r['Player_ID'],'match_statistics',key,r[key]) for r in rows for key in r.keys() if key not in metadata]
        elif provider=='opta':values=[tuple(r) for r in data.execute('SELECT team_id,player_id,feed_name,stat_type,stat_value FROM op_player_stat WHERE match_id=? ORDER BY team_id,player_id,feed_name,stat_type LIMIT 40001',(sid,))]
        else:raise ValueError('unsupported_provider')
        if len(values)>40000:raise ValueError('player_statistics_limit')
        for tid,pid,feed,key,value in values:
            team=identity('team',tid);player=identity('player',pid)
            try:metric=observation(provider,key,value,entity_scope='player')
            except ValueError:issues.append({'code':'invalid_metric_value','player_id':player});continue
            if metric is None:unmapped.add((feed,key));continue
            series=stable_id('series',provider,'player-statistics:'+feed)
            if metric['value'] is not None and definition_status(provider,key,'player')=='unverified':
                issues.append({'code':'unverified_metric_definition','player_id':player,
                               'metric_code':metric['metric_code'],'series_id':series})
            # Keep unresolved players distinct internally without exposing source IDs.
            grain=(str(pid),series,metric['metric_code'])
            if grain in seen:raise ValueError('standard_metric_key_collision')
            seen.add(grain)
            result.append({'team_id':team,'player_id':player,'series_id':series,'period':'full_time',**metric})
            if _source_refs is not None:_source_refs.append((str(tid),str(pid)))
    out=envelope(match_id,provider,'player-statistics',result,issues);out['quality']['unmapped_field_count']=len(unmapped);return out
