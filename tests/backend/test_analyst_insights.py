from copy import deepcopy
from backend.studio.analyst_insights import enrich, regional_card, lineup_card
from backend.studio.analyst_workflow import findings, roster_diff, assess


def sample():
    teams=[]
    for i in range(2):
        players=[{'id':f'{i}-{j}','name':f'P{i}{j}','minutes':300 if j<2 else 60,'position':'Midfielder','progressive':10,'xg':.1,'xa':None,'passes':20,'model_xt':.05} for j in range(3)]
        chains=[{'event_id':f'{i}-{j}','match_id':'m','direction':'attack','creator_zone':11 if j<3 else 14,'method':'pass','xg':.2,'creator_id':players[0]['id'],'player_id':players[1]['id'],'sequence':[],'period':1,'home_players':11,'away_players':11,'penalty':False,'creator_linked':True} for j in range(5)]
        teams.append({'id':str(i),'name':f'T{i}','minutes':450,'samples':[{'id':str(j)} for j in range(5)],'players':players,'chains':chains,'methods':[],'missing_layers':[]})
    for i in range(2):teams[i]['chains']+= [dict(c,direction='defence') for c in teams[1-i]['chains']]
    old={'type':'predicted','teams':[{'side':s,'players':[{'id':f'{i}-0','name':'old'}]} for i,s in enumerate(('home','away'))]}
    new={'type':'standard','teams':[{'side':s,'players':[{'id':f'{i}-2','name':'new'}]} for i,s in enumerate(('home','away'))]}
    r={'match':{'id':'m'},'phase':'prematch','teams':teams,'lineup':new,'lineup_baseline':old,'lineup_changes':roster_diff(old,new),'filters':{'eleven':1},'hypotheses':[]}
    return r


def test_source_policy_never_uses_fotmob_xa_and_missing_stays_missing():
    r=sample();r['fotmob_stats']={'xa':99}
    enrich(r)
    assert r['source_policy']['fotmob_statistics_fallback'] is False
    assert all(t['known_players']==0 for t in r['metric_coverage']['xa']['teams'])
    assert all(p['xa'] is None for t in r['teams'] for p in t['players'])
    assert len(r['hypotheses'])==5
    assert len([c for c in r['hypotheses'] if c['topic']=='lineup'])==2
    assert r['tactical_analysis']['version']=='tactical-analysis.v2'


def test_lineup_small_sample_uses_common_history_not_arithmetic_prediction():
    r=sample();c,d=lineup_card(r,r['teams'][0],r['lineup_changes']['teams'][0],r['lineup_baseline'])
    assert d['added'][0]['small_sample'] and d['not_retained_chances']==5
    assert '不能据此判断升级或降级' in c['limitation']
    assert '新伤停' in d['note']
    assert c['rule']['player_ids']==['0-2']


def test_regional_priority_uses_value_not_count_and_zero_not_missing():
    r=sample();t,o=r['teams']
    r['lineup']['teams'][0]['players']=[{'id':'0-0','name':'creator'}]
    # Zone14 has fewer chances but much higher value on both sides.
    for c in t['chains']+o['chains']:
        c['xg']=.01 if c['creator_zone']==11 else .4
    card=regional_card(r,t,o,0)
    assert card['rule']['value']==14
    assert card['assessment']=='可利用的对位'
    for c in o['chains']:
        if c['direction']=='defence' and c['creator_zone']==14:c['xg']=None
    card=regional_card(r,t,o,0)
    assert card['rule']['value']==11
    assert card['assessment']=='不能确认明显优势'


def test_postmatch_lineup_analysis_keeps_prematch_history():
    pre=sample();post=deepcopy(pre);post['phase']='postmatch'
    pre['teams'][0]['players'][2].update(xa=.34,xa_match_count=2,xa_sources=[{'match_id':'historical','scope':'whole_match'}])
    post['teams'][0]['players'][2]['progressive']=999
    post['teams'][0]['players'][2]['xa']=99
    enrich(post,pre)
    assert post['lineup_impacts'][0]['added'][0]['metrics']['progressive']==10
    added=post['lineup_impacts'][0]['added'][0]
    assert added['metrics']['xa']==.34 and added['xa_match_count']==2
    assert added['xa_sources'][0]['match_id']=='historical'
    assert 'xa' not in added['per90']
    assert post['lineup_impacts'][0]['added'][0]['metrics']['progressive']==10
    actual_card=next(c for c in post['hypotheses'] if c.get('topic')=='lineup' and c['rule']['team_id']=='0')
    assert actual_card['measurements']['entrants'][0]['progressive']==999
    assert actual_card['measurements']['entrants'][0]['xa']==99
    assert all('赛前视角' not in c.get('condition','') for c in post['hypotheses'])


def test_personnel_hypothesis_checks_observed_events_not_final_score():
    r=sample();enrich(r);h=r['hypotheses'][0]
    actual=deepcopy(r)
    for t in actual['teams']:
        t['chains']=[c for c in t['chains'] if c['direction']=='attack']
    actual['teams'][0]['chains'][0]['creator_id']='0-2'
    check=assess([h],actual)[0]
    assert check['support_events'][0]['creator_id']=='0-2'
    assert check['counter_events']==[] and len(check['alternative_events'])==4
    actual['teams'][0]['chains'][1]['creator_linked']=False
    assert assess([h],actual)[0]['status']=='部分支持'
