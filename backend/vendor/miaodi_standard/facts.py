"""Typed, bounded read projections over source facts; no full event-store copy."""
import json
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from .contract import stable_id,source_id,name_or_none
from .metrics import observation, NON_METRIC_FIELDS
from .identity_lookup import resolve

PERIODS={'All':'full_time','FirstHalf':'first_half','SecondHalf':'second_half',
         'full':'full_time','first_half':'first_half','second_half':'second_half'}


def read(path):
    c=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
    c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
    deadline=time.monotonic()+10;c.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
    return c


@contextmanager
def snapshots(registry, source, connections=None):
    """Reuse caller-owned read transactions when composing one report.

    A borrowed connection must already be query-only and in a transaction.
    Ownership stays with the caller; this context never commits or closes it.
    """
    if connections is not None:
        if len(connections) != 2:
            raise ValueError('two_read_snapshots_required')
        for conn in connections:
            if not conn.in_transaction or conn.execute('PRAGMA query_only').fetchone()[0] != 1:
                raise ValueError('readonly_snapshot_required')
        yield connections
    else:
        with closing(read(registry)) as ids, closing(read(source)) as data:
            yield ids, data


def canonical_id(c,kind,provider,value):
    try:value=source_id(value)
    except ValueError:return None
    r=c.execute('SELECT entity_id FROM source_ids WHERE kind=? AND provider=? AND source_id=?',(kind,provider,value)).fetchone()
    return resolve(c,r[0]) if r else None


def identity_lookup(c, provider):
    """Memoize entity resolution only within one immutable read transaction."""
    cache = {}
    def lookup(kind, value):
        key = (kind, type(value), value)
        if key not in cache:
            cache[key] = canonical_id(c, kind, provider, value)
        return cache[key]
    return lookup


def match_source(c,match_id,provider):
    match_id=resolve(c,match_id)
    r=c.execute("SELECT source_id FROM source_ids WHERE entity_id=? AND kind='match' AND provider=?",(match_id,provider)).fetchone()
    if not r:raise LookupError('match_unavailable_for_series')
    return r[0]


def player_identity_status(c,provider,value):
    if canonical_id(c,'player',provider,value) is None:return 'unresolved'
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='identity_master_records'").fetchone():return 'mapped'
    row=c.execute("SELECT 1 FROM identity_master_records WHERE kind='player' AND provider=? AND source_id=?",(provider,source_id(value))).fetchone()
    return 'mapped' if row else 'reference_only'


def team_statistics(registry,source,provider,match_id,*,_connections=None,_source_refs=None):
    # Optional private join references are deliberately outside the response.
    with snapshots(registry,source,_connections) as (ids,data):
        match_id=resolve(ids,match_id)
        identity=identity_lookup(ids,provider)
        sid=match_source(ids,match_id,provider);observations=[];issues=[];unmapped=set()
        if provider=='fotmob':
            rows=data.execute('SELECT Team_ID,Period,Goals,extra_json FROM fact_team_match_stats WHERE Match_ID=? LIMIT 201',(sid,)).fetchall()
            if len(rows)>200:raise ValueError('match_statistics_row_limit')
            values=[]
            for row in rows:
                metrics=json.loads(row['extra_json'] or '{}')
                if not isinstance(metrics,dict):raise ValueError('invalid_source_statistics')
                # A core-column goal value owns this field; JSON cannot override it.
                metrics['Goals']=row['Goals']
                values.extend((row['Team_ID'],'match_statistics',row['Period'],key,value) for key,value in metrics.items())
        elif provider=='opta':
            values=[tuple(r) for r in data.execute('SELECT team_id,feed_name,segment,stat_type,stat_value FROM op_team_stat WHERE match_id=? LIMIT 10001',(sid,))]
        else:raise ValueError('unsupported_provider')
        if len(values)>10000:raise ValueError('match_statistics_limit')
        seen=set()
        for tid,feed,period,key,value in values:
            if key in NON_METRIC_FIELDS.get(provider,set()):continue
            team=identity('team',tid)
            if team is None:issues.append({'code':'unmapped_team_identity'});continue
            phase=PERIODS.get(period)
            if phase is None:issues.append({'code':'unmapped_period'});continue
            try:metric=observation(provider,key,value,entity_scope='team')
            except ValueError:issues.append({'code':'invalid_metric_value'});continue
            if metric is None:unmapped.add((feed,key));continue
            series=stable_id('series',provider,'team-statistics:'+feed)
            grain=(team,series,phase,metric['metric_code'])
            if grain in seen:raise ValueError('standard_metric_key_collision')
            seen.add(grain)
            observations.append({'team_id':team,'series_id':series,'period':phase,**metric})
            if _source_refs is not None:_source_refs.append((str(tid),phase))
    return {'schema_version':'miaodi-team-statistics.v2','match_id':match_id,
            'observations':observations,'quality':{'issues':issues,'unmapped_field_count':len(unmapped)}}


def flag(value):
    if value is None:return None
    if value in (0,1):return bool(value)
    raise ValueError('invalid_boolean')


def lineups(registry,source,provider,match_id):
    with closing(read(registry)) as ids,closing(read(source)) as data:
        match_id=resolve(ids,match_id)
        sid=match_source(ids,match_id,provider)
        if provider=='fotmob':
            rows=data.execute('SELECT Team_ID team_id,Player_ID player_id,player_name,shirt_number,is_starter,is_captain FROM fact_match_lineup WHERE Match_ID=? ORDER BY Team_ID,Player_ID LIMIT 201',(sid,)).fetchall()
        elif provider=='opta':
            rows=data.execute('SELECT l.team_id,l.player_id,p.match_name player_name,l.shirt_number,l.is_starter,l.is_captain FROM op_match_player l LEFT JOIN op_player p ON p.player_id=l.player_id WHERE l.match_id=? ORDER BY l.team_id,l.player_id LIMIT 201',(sid,)).fetchall()
        else:raise ValueError('unsupported_provider')
        if len(rows)>200:raise ValueError('lineup_limit')
        result=[]
        for r in rows:
            player=canonical_id(ids,'player',provider,r['player_id']);team=canonical_id(ids,'team',provider,r['team_id'])
            if team is None:raise ValueError('unmapped_team_identity')
            result.append({'team_id':team,'player_id':player,'player_name':name_or_none(r['player_name'],r['player_id']),
                           'shirt_number':str(r['shirt_number']) if r['shirt_number'] is not None else None,
                           'is_starter':flag(r['is_starter']),'is_captain':flag(r['is_captain']),
                           'identity_status':player_identity_status(ids,provider,r['player_id'])})
    return {'schema_version':'miaodi-lineup.v1','match_id':match_id,'players':result}
