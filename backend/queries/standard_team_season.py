"""Readonly, bounded website season adapters. Legacy/raw data stays unchanged."""
import json
from decimal import Decimal,ROUND_HALF_EVEN
from backend.vendor.miaodi_standard.contract import stable_id,number
from backend.vendor.miaodi_standard.facts import snapshots,identity_lookup
from backend.vendor.miaodi_standard.identity_lookup import resolve
from backend.vendor.miaodi_standard.season_metrics import season_observation
from backend.queries import league_stats,matches
from backend.queries.standard_player_season import _team
from backend.queries.team_season_definitions import AGGREGATES,RATIO_UNITS,STANDINGS_FIELDS
from backend.metrics.registry import get_metric
from backend.silver.ratio_metrics import build_team_season_ratios
from backend.queries.window import resolve_match_window


def _issue(issues,code,field):issues.append({'code':code,'field':field})


def _safe(value,issues,field,*,count=False,low=0,high=None):
    try:return number(value,'count' if count else 'number',low,high)
    except ValueError:_issue(issues,'invalid_metric_value',field);return None


def _reference(row,lookup,record,issues):
    team=_team(row,lookup);team['record_id']=record
    if team['team_id'] is None:_issue(issues,'unresolved_identity','team_id')
    return team


def _meta(raw):
    try:result=json.loads(raw or '{}')
    except (TypeError,ValueError):return {}
    return result if isinstance(result,dict) else {}


def _team_ratios(data,league_id,season,raw,*,recency,venue):
    if season is None or not raw['rows']:return {},{}
    if recency is None and venue=='all':
        rows=data.execute('''SELECT Team_ID,metric_key,numerator_sum,denominator_sum,
            paired_matches,matches_played,sample_count,methodology_version
            FROM silver_team_season_ratios WHERE League_ID=? AND Season=? LIMIT 5001''',
            (league_id,season)).fetchall()
    else:
        pairs=resolve_match_window(data,league_id,season,recency=recency,venue=venue)
        counts={r['team']['team_id']:r['matches_played'] for r in raw['rows']}
        rows=build_team_season_ratios(data,league_id,season,counts,window_pairs=pairs)
    if len(rows)>5000:raise ValueError('team_ratio_limit')
    values={};issues={};seen=set()
    for r in rows:
        tid=str(r['Team_ID']);key=r['metric_key'];grain=(tid,key)
        if grain in seen:raise ValueError('duplicate_team_ratio')
        seen.add(grain);quality=issues.setdefault(tid,[])
        if key not in RATIO_UNITS:_issue(quality,'unmapped_derived_metric','ratios');continue
        definition=get_metric(key)
        if r['methodology_version']!=definition.methodology_version:
            values.setdefault(tid,{})[key]=None;_issue(quality,'unverified_derived_definition','ratios');continue
        try:
            n=number(r['numerator_sum'],low=None if key in ('finishing_delta','gk_saves_above_expected') else 0)
            d=number(r['denominator_sum'],low=0)
            paired=number(r['paired_matches'],'count',0);played=number(r['matches_played'],'count',0)
            sample=number(r['sample_count'],'count',0)
            if paired is not None and played is not None and paired>played:raise ValueError('pair_count_exceeds_matches')
            value=float((Decimal(str(definition.display_scale))*Decimal(str(n))/Decimal(str(d))).quantize(Decimal('0.0001'),rounding=ROUND_HALF_EVEN)) if n is not None and d is not None and d>0 else None
            if RATIO_UNITS[key]=='percent' and value is not None and not 0<=value<=100:raise ValueError('invalid_percentage')
        except ValueError:
            values.setdefault(tid,{})[key]=None;_issue(quality,'invalid_metric_value','ratios');continue
        if paired is None or played is None:_issue(quality,'missing_pairing_count','ratios')
        values.setdefault(tid,{})[key]={'value':value,'numerator':n,'denominator':d,
            'paired_matches':paired,'matches_played':played,'sample_count':sample,'unit':RATIO_UNITS[key],
            'definition_version':'miaodi-team-'+key+'.'+r['methodology_version'],
            'sample_policy':'paired_source_observations'}
    return values,issues


def team_season_view(registry,source,competition_id,league_id,season,kind,*,table_type='all',recency=None,venue='all'):
    with snapshots(registry,source) as (ids,data):
        competition_id=resolve(ids,competition_id);lookup=identity_lookup(ids,'fotmob')
        if lookup('competition',league_id)!=competition_id:raise ValueError('competition_identity_mismatch')
        if kind=='standings':
            raw=matches.standings(data,league_id,season,table_type=table_type)
            if len(raw['rows'])>2000:raise ValueError('standings_row_limit')
            rows=[];seen=set()
            for index,r in enumerate(raw['rows']):
                source_id=r['team']['team_id'];grain=(table_type,r['group_name'],str(source_id))
                if source_id is not None and grain in seen:raise ValueError('duplicate_standings_team')
                seen.add(grain);quality=[]
                record=stable_id('record',competition_id,'standings:'+str(raw['season'])+':'+table_type+':'+str(r['group_name'])+':'+(str(source_id) if source_id is not None else 'unresolved-row-'+str(index)))
                row={'record_id':record,'team':_reference(r['team'],lookup,record,quality),
                     'group_name':r['group_name'],'qual_color':r['qual_color'],'issues':quality}
                for key,source_key in STANDINGS_FIELDS.items():
                    signed=key in ('goal_difference','points','points_above_expected','position_difference')
                    decimal=key in ('expected_goals_for','expected_goals_against','expected_points','points_above_expected','position_difference')
                    row[key]=_safe(r.get(source_key),quality,key,count=not decimal,low=None if signed else 1 if key in ('position','expected_position') else 0)
                rows.append(row)
            return {'schema_version':'miaodi-website-standings.v1','competition_id':competition_id,
                'table_type':table_type,'season':raw['season'],'available_seasons':raw['available_seasons'],
                'season_finished':raw['season_finished'],'rows':rows,'empty_reason':None if rows else '该联赛暂无积分榜数据'}
        if kind!='team-stats':raise ValueError('unknown_team_season_view')
        raw=league_stats.team_season_stats(data,league_id,season,recency=recency,venue=venue,include_ratios=False,source_metadata=True)
        if len(raw['rows'])>1000 or len(raw['boards'])>100:raise ValueError('team_season_limit')
        ratio_values,ratio_issues=_team_ratios(data,league_id,raw['season'],raw,recency=recency,venue=venue)
        rows=[];seen=set()
        for index,r in enumerate(raw['rows']):
            tid=r['team']['team_id']
            if tid is not None and str(tid) in seen:raise ValueError('duplicate_team_aggregate')
            seen.add(str(tid));quality=list(ratio_issues.get(str(tid),[]))
            record=stable_id('record',competition_id,'team-season:'+str(raw['season'])+':'+(str(tid) if tid is not None else 'unresolved-row-'+str(index)))
            row={'record_id':record,'team':_reference(r['team'],lookup,record,quality),
                 'team_color':r.get('team_color'),'ratios':ratio_values.get(str(tid)),'issues':quality}
            for field,(unit,aggregation) in AGGREGATES.items():
                row[field]=_safe(r.get(field),quality,field,count=unit=='count' and aggregation=='season_total',high=100 if unit=='percent' else None)
            rows.append(row)
        boards=[];categories={'Top Stat':'focus','Attacking':'attack','Defending':'defence','Discipline':'discipline'}
        for b in raw['boards']:
            definition=season_observation('team',b['stat_name'],None)
            if definition is None:raise ValueError('unregistered_team_board')
            entries=[];groups=set();board_seen=set()
            for index,r in enumerate(b['entries']):
                tid=r['team']['team_id'];quality=[];definition_issues=[];meta=_meta(r.get('_source_metadata'))
                if tid is not None and str(tid) in board_seen:raise ValueError('duplicate_team_board')
                board_seen.add(str(tid));groups.add(categories.get(meta.get('category'),'unclassified'))
                try:metric=season_observation('team',b['stat_name'],r['value'],meta,issues=definition_issues)
                except ValueError:metric=season_observation('team',b['stat_name'],None,meta);_issue(quality,'invalid_metric_value','value')
                for code in definition_issues:_issue(quality,code,'aggregation')
                if metric['aggregation']=='unverified':_issue(quality,'unverified_aggregation','aggregation')
                if metric['unit'].startswith('unverified'):_issue(quality,'unverified_unit','unit')
                decimal=meta.get('stat_decimals');decimal=decimal if type(decimal) is int and 0<=decimal<=6 else None
                record=stable_id('record',competition_id,'team-board:'+str(raw['season'])+':'+b['stat_name']+':'+(str(tid) if tid is not None else 'unresolved-row-'+str(index)))
                entries.append({'record_id':record,'team':_reference(r['team'],lookup,record,quality),
                    'rank':_safe(r['rank'],quality,'rank',count=True,low=1),
                    **{k:metric[k] for k in ('value','unit','aggregation','definition_version')},
                    'display_decimals':decimal,'team_color':r.get('team_color'),'issues':quality})
            boards.append({'metric_code':definition['metric_code'],
                'series_id':stable_id('series','fotmob','season-statistics:team:'+b['stat_name']),
                'label_zh':{'goals_team_match':'进球','goals_conceded_team_match':'失球'}.get(b['stat_name'],b['label_zh']),
                'category':next(iter(groups)) if len(groups)==1 else 'unclassified','entries':entries})
        return {'schema_version':'miaodi-website-team-season.v1','competition_id':competition_id,
            'season':raw['season'],'available_seasons':raw['available_seasons'],'rows':rows,'boards':boards,
            'metric_definitions':[{'metric_code':key,'unit':unit,'aggregation':aggregation,
                'definition_version':'miaodi-team-season-aggregates.v1',
                'sample_policy':'source_derived_metric_coverage_unavailable'} for key,(unit,aggregation) in AGGREGATES.items()],
            'empty_reason':raw.get('empty_reason') or (None if rows or boards else '该联赛暂无球队赛季数据'),
            'recency':recency,'venue':venue}
