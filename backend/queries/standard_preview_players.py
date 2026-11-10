"""Five-match observed player totals. Missing observations never become zero."""
from collections import defaultdict
from backend.vendor.miaodi_standard.contract import number,source_id
from backend.queries.player_form import _last_n_match_ids,MIN_APPEARANCES,TOP_N
from backend.queries.league_stats import _player_i18n_map
from .standard_preview_people import person,issue

BLOCKS=[('dribbles','破局点','dribbles_completed',('dribbles_succeeded',)),
        ('creating','组织核心','chances_created',('chances_created',)),
        ('defending','防守支柱','tackles_plus_interceptions',('tackles','interceptions'))]

def numeric(value,*,count=False,signed=False):
    try:return number(value,'count' if count else 'number',None if signed else 0)
    except ValueError:return None


def player_windows(ids,core,mid,match,lookup):
    shares={};keepers={};i18n=_player_i18n_map(core);before=match.get('kickoff_at_utc') or match['date_utc']
    for side in ('home','away'):
        tid=match[side]['team_id'];mids=_last_n_match_ids(core,tid,match['league_id'],before,5)
        rows=[]
        if mids:
            rows=[dict(r) for r in core.execute('''SELECT Match_ID,Player_ID,player_name,minutes_played,
                dribbles_succeeded,chances_created,"matchstats.headers.tackles" AS tackles,interceptions,
                is_goalkeeper,saves,goals_conceded,expected_goals_on_target_faced,goals_prevented
                FROM fact_player_match_stats WHERE Team_ID=? AND Match_ID IN ('''+','.join('?' for _ in mids)+') LIMIT 501',(tid,*mids))]
            if len(rows)>500:raise ValueError('player_window_row_limit')
        grouped=defaultdict(list)
        for index,r in enumerate(rows):
            try:key=source_id(r['Player_ID'])
            except ValueError:key='unresolved-row:'+str(r['Match_ID'])+':'+str(index)
            if numeric(r['minutes_played']) is not None and r['minutes_played']>0:grouped[key].append(r)
        blocks=[]
        for code,title,metric,fields in BLOCKS:
            totals=[];complete=True
            for key,records in grouped.items():
                observed=[]
                for r in records:
                    values=[numeric(r[f],count=True) for f in fields]
                    if all(v is not None for v in values):observed.append(sum(values))
                total=sum(observed) if observed else None;complete=complete and len(observed)==len(records)
                totals.append((key,records,total,len(observed)))
            denominator=sum(v for _,_,v,_ in totals if v is not None);eligible=sorted((x for x in totals if len(x[1])>=MIN_APPEARANCES and x[2] is not None and x[2]>0),key=lambda x:(-x[2],x[0]))[:TOP_N];out=[]
            for key,records,total,observed in eligible:
                row=person(ids,lookup,mid,{'id':records[0]['Player_ID'],'name':max((r['player_name'] or '' for r in records))},f'window/{side}/{code}/{key}',i18n)
                full=observed==len(records)
                row.update(pct=round(100*total/denominator,1) if full and denominator>0 else None,
                    appearances=len(records),minutes=sum(r['minutes_played'] for r in records),
                    count=total if full else None,observed_matches=observed)
                if not full:row['issues'].append(issue('incomplete_metric_coverage','count',row['record_id']))
                out.append(row)
            blocks.append({'id':code,'title':title,'metric':'占已观测球员记录总量','metric_code':metric,
                'unit':'count','definition_version':'miaodi-player-window-shares.v1',
                'denominator_policy':'reported_player_observations','coverage_complete':complete,'rows':out})
        shares[side]=blocks;out=[]
        for key,records in grouped.items():
            selected=[r for r in records if r['is_goalkeeper']==1]
            if not selected:continue
            row=person(ids,lookup,mid,{'id':selected[0]['Player_ID'],'name':max((r['player_name'] or '' for r in selected))},f'keeper/{side}/{key}',i18n)
            row.update(appearances=len(selected))
            for target,source,count,signed in [('saves','saves',True,False),('goals_conceded','goals_conceded',True,False),('xgot_faced','expected_goals_on_target_faced',False,False),('goals_prevented','goals_prevented',False,True)]:
                values=[numeric(r[source],count=count,signed=signed) for r in selected];known=[v for v in values if v is not None]
                full=len(known)==len(selected)
                # Partial xGOT is retained with its separate completeness flag;
                # partial saves/goals/prevented cannot masquerade as window totals.
                row[target]=round(sum(known),2) if known and (full or target=='xgot_faced') else None
                if target=='xgot_faced':row['xgot_faced_complete']=full
                if not full:row['issues'].append(issue('incomplete_metric_coverage',target,row['record_id']))
            out.append(row)
        keepers[side]=sorted(out,key=lambda r:(-r['appearances'],r['record_id']))
    return shares,keepers
