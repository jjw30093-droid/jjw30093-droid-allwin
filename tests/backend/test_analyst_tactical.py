from copy import deepcopy
from uuid import uuid4
import pytest
from backend.studio.analyst_tactical import attach,postmatch,creators,progression,goalkeeper,transitions
from backend.studio.analyst_workflow import validate_report,assess
from backend.studio.prematch import lineup_position


def fixture():
    mid=str(uuid4());teams=[]
    for tid in ('home','away'):
        chains=[dict(event_id=tid+str(i),match_id=mid,direction='attack',xg=v,goal=i==0,penalty=i==0,
                     player_id=tid+'p',creator_id=None,creator_zone=12,method='点球' if i==0 else '传中参与',
                     period=1,minute=20+i,second=0,sequence=[],chain_complete=False,home_players=11,away_players=11)
                for i,v in enumerate((.78,.04,.03))]
        teams.append(dict(id=tid,name=tid,players=[dict(id=tid+'p',name='P',minutes=90)],chains=chains,
                          samples=[dict(id=mid)],minutes=90,missing_layers=[],report_extensions={},analyst_matches=[]))
    return dict(match={'id':mid},phase='postmatch',teams=teams,filters={},hypotheses=[])

def test_transition_conclusion_uses_selected_regain_shots_not_whole_match_pressing():
    r=fixture();t=r['teams'][0];shot=t['chains'][1]
    t['analyst_matches']=[{'pressing':{'quick_transition_shots_proxy':999,'quick_transition_xg_known':100,'counterpress_regains_within_5s_proxy':200}}]
    t['transitions']=[shot,shot]  # duplicate references are not duplicate chances
    c=transitions(r,t)[0]
    assert c['measurements']['own_shots']==1 and c['measurements']['own_xg']==.04
    assert c['measurements']['opponent_shots']==0 and c['measurements']['opponent_xg'] is None
    assert '999' not in c['text'] and '5秒内再夺回' not in c['text']
    assert '不代表所有丢球' in c['limitation']
    t.pop('transitions');assert transitions(r,t)==[]


def test_postmatch_is_actual_evidence_and_separates_penalty_quality():
    r=fixture();attach(r,r,[])
    assert any(c['topic']=='goal' for c in r['hypotheses'])
    c=next(c for c in r['hypotheses'] if c['topic']=='finishing')
    assert c['measurements']['shots']==2
    assert c['measurements']['mean_xg']==pytest.approx(.035)
    assert c['assessment']=='机会质量风险'
    validate_report(r)


def test_xa_components_missing_not_imputed_and_xt_negative_retained():
    r=fixture();t=r['teams'][0];e=t['chains'][0]
    t['analyst_detail']={'matches':[{'xa':[{'total':.8,'openplay':None,'setplay':.2}],
        'xt_actions':[dict(e,xt_added=-.1),dict(t['chains'][1],xt_added=.2)]}]}
    xa,xt=creators(r,t)
    assert xa['measurements']['known_records']['openplay']==0
    assert '运动战xA 未提供' in xa['text']
    assert xt['measurements']['negative']==-.1 and xt['counter_events'][0]['event_id']==e['event_id']


def test_evidence_validation_rejects_match_collision_and_unseen_specialist_event():
    r=fixture();attach(r,r,[])
    bad=deepcopy(r)
    bad['tactical_analysis']['findings'][0]['events'][0]=dict(bad['tactical_analysis']['findings'][0]['events'][0],match_id=str(uuid4()))
    with pytest.raises(ValueError,match='报告以外'):validate_report(bad)


def test_goalkeeper_excludes_blocked_and_wide_zero_xgot():
    r=fixture();t=r['teams'][0]
    t['analyst_detail']={'matches':[{'goalkeepers':[{'player_id':'homep','shots':[
        dict(t['chains'][0],outcome='goal',xgot=.5),dict(t['chains'][1],outcome='blocked',xgot=0),
        dict(t['chains'][2],outcome='saved',xgot=.2)],'distribution':[]}]}]}
    c=goalkeeper(r,t)[0]
    assert c['measurements']['shots_on_target']==2 and c['measurements']['xgot']==pytest.approx(.7)


def test_lineup_coordinates_preserve_provider_direction_and_reject_nonfinite():
    assert lineup_position({'pos_x':.5,'pos_y':.1})['pos_y']==.1
    assert lineup_position({'pos_x':.5,'pos_y':.87})['pos_y']==.87
    assert lineup_position({'pos_x':float('nan'),'pos_y':.1})['coordinate_frame'] is None


def test_descriptive_judgment_never_misclassified_as_method_prediction():
    r=fixture();c=postmatch(r,r['teams'][0])[0]
    assert assess([c],r)[0]['status']=='无法判断'


def test_postmatch_homepage_keeps_both_teams_and_lineup_changes_after_many_goals():
    r=fixture()
    for t in r['teams']:
        for c in t['chains']:c['goal']=True
    r['lineup_changes']={'teams':[{'side':side,'added':[{'id':t['players'][0]['id']}]} for side,t in zip(('home','away'),r['teams'])]}
    attach(r,r,[])
    assert len(r['hypotheses'])==5
    assert {(c['rule']['team_id'],c['topic']) for c in r['hypotheses'] if c['topic']!='goal'}=={
        ('home','finishing'),('away','finishing'),('home','lineup'),('away','lineup')}
    assert all('本场终结效率高' in c['title'] for c in r['hypotheses'] if c['topic']=='finishing')
