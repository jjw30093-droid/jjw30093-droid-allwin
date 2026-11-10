"""PDF detail respects source frames, missingness, immutable evidence and scale."""
from copy import deepcopy
from reportlab.graphics.shapes import Drawing, Circle, Rect, String
from backend.studio.analyst_pdf_detail import append_detail_sections, grouped_xa, lineup_xy, finding_evidence


def test_xa_components_keep_independent_coverage_not_residual_imputation():
    got=grouped_xa([{'xa':[{'player_id':'a','name':'A','total':.5,'openplay':None,'setplay':.1}]},
                    {'xa':[{'player_id':'a','name':'A','total':None,'openplay':.2,'setplay':None}]}])[0]
    assert got['total']==.5 and got['openplay']==.2 and got['setplay']==.1
    assert got['coverage']=={'total':1,'openplay':1,'setplay':1}
    assert grouped_xa([{'xa':[{'player_id':'a','name':'A','total':None}]}])[0]['total'] is None


def test_lineup_source_axes_goalkeeper_stays_own_end_and_missing_not_inferred():
    gk={'pos_x':.5,'pos_y':.1,'coordinate_frame':'provider_vertical_0_1'}
    assert lineup_xy(gk,0)==(10,50)
    assert lineup_xy(gk,1)==(90,50)
    assert lineup_xy({'position':'Goalkeeper'},0) is None
    assert lineup_xy(dict(gk,pos_y=110),0) is None


def test_finding_support_counter_and_alternative_are_separate():
    calls=[];ref={'event_id':'pass1','match_id':'match1','period':1,'minute':47,'second':5}
    finding_evidence({'events':[ref],'counter_events':[dict(ref,event_id='counter')],
                      'alternative_events':[dict(ref,event_id='alternative')]},lambda text,*_:calls.append(text))
    joined=' '.join(calls)
    assert '45+2:05' in joined and 'pass1' in joined and 'counter' in joined and 'alternative' in joined
    assert '其他路线（不等同反驳）' in joined and '反驳过程：1' in joined


def test_detail_keeps_negative_xt_and_foul_scope_without_mutating_snapshot():
    event={'match_id':'m','event_id':'e','period':1,'minute':10,'second':0,'player_id':'p',
           'player_name':'PLAYER','x':20,'y':30,'end_x':40,'end_y':50,'direction':'attack','success':True}
    team={'name':'HOME','id':'h','players':[{'id':'p','name':'PLAYER'}], 'analyst_detail':{'matches':[{
        'match_id':'m','xt_actions':[dict(event,xt_added=-.5,model_version='v1'),dict(event,event_id='e2',xt_added=.2,model_version='v1')],
        'action_zones':[dict(event,kind='foul',success=None)]}]}}
    report={'teams':[team,{'name':'AWAY','id':'a','players':[]}],'hypotheses':[]}
    before=deepcopy(report);calls=[];drawings=[]
    def text(x,*_):calls.append(str(x))
    append_detail_sections(report,drawings,text,lambda *a,**kw:None,text,lambda _:Drawing(495,335),
                           'Helvetica',['#b42338','#7037a7'],lambda n:'未提供' if n is None else str(n))
    assert report==before
    assert any('负贡献 -0.5' in x for x in calls)
    assert any('不能加总为独立犯规事件' in x for x in calls)
    # Negative action markers are hollow; the positive action is filled.
    circles=[s for d in drawings if isinstance(d,Drawing) for s in d.contents if isinstance(s,Circle)]
    assert any(s.r>3 and s.fillColor.hexval()=='0xffffff' for s in circles)


def test_goalkeeper_xgot_coverage_counts_only_identified_on_target_shots():
    from backend.studio.analyst_pdf_detail import render_goalkeepers
    shots=[{'outcome':'goal','goal':True,'xgot':.8},
           {'outcome':'saved','xgot':0},  # a real zero remains covered
           {'outcome':'saved_to_post','xgot':.2},
           {'outcome':'on_target','xgot':None},
           {'outcome':'blocked','xgot':0},
           {'outcome':'off_target','xgot':0},
           {'outcome':'unknown','xgot':.9}]
    details=[[{'goalkeepers':[{'player_id':'keeper','name':'KEEPER','shots':shots}]}],
             [{'goalkeepers':[{'player_id':'other','name':'OTHER','shots':[{'outcome':'blocked','xgot':0}]}]}]]
    before=deepcopy(details);tables=[];charts=[];notes=[]
    render_goalkeepers({'teams':[{'name':'HOME'},{'name':'AWAY'}]},details,[],
        lambda text,*_:notes.append(text),lambda h,rows,*_:tables.append((h,rows)),lambda _:None,
        'Helvetica',['#b42338','#7037a7'],str,lambda *args:charts.append(args),lambda *args:None)
    assert details==before
    assert tables[0][0][-1]=='覆盖 / 射正'
    assert tables[0][1]==[['HOME / KEEPER',1.0,1,'3/4'],['AWAY / OTHER',None,None,'0/0']]
    assert charts[0][1][0][1]==[1.0,None]
    assert any('射偏、封堵即使xGOT=0也不进入分母' in x for x in notes)


def test_legacy_event15_does_not_assume_blocks_are_saved_shots():
    from backend.studio.analyst_pdf_detail import legacy_on_target_shots
    rows=[{'match_id':'m','event_id':name,'event_type':kind,'outcome':1,'xgot':0}
          for name,kind in [('wide',13),('blocked',15),('saved_zero',15),('unknown',15),('goal',16)]]
    teams=[{'analyst_detail':{'matches':[{'shots':[
        {'match_id':'m','event_id':'blocked','outcome':'blocked'},
        {'match_id':'m','event_id':'saved_zero','outcome':'on_target'}]}]}}]
    confirmed,unknown=legacy_on_target_shots(rows,teams)
    assert [e['event_id'] for e in confirmed]==['saved_zero','goal']
    assert [e['event_id'] for e in unknown]==['unknown']
