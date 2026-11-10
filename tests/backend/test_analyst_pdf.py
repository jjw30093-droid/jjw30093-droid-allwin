"""Report exports must consume stored evidence, not synthesize fresh metrics."""
from copy import deepcopy

from reportlab.graphics.shapes import Drawing

from backend.studio.analyst_pdf import _append_analysis_sections


def fixture():
    event={'match_id':'match1','event_id':'event1','period':1,'minute':12,'second':3,
           'player_id':'a','player_name':'PLAYER_A','team_id':'home','x':30,'y':25}
    team={'id':'home','name':'HOME','players':[{'id':'a','name':'PLAYER_A','minutes':90,
          'position':'Midfielder','xa':None,'per90':{'progressive':4}}],
          'samples':[{'id':'match1','kickoff_at':'2026-10-01','opponent':'AWAY','venue':'home','shots':2,'xg':.3,'xga':None}],
          'routes':[{'label':'中场','possessions':10,'shot_possessions':2,'xg':.3}],
          'attack_patterns':[{'name':'传中参与','attacks':5,'shot_attacks':1,'shots':1,'xg':.1}],
          'carries':[dict(event,progressive=True)],'transitions':[dict(event,direction='attack',xg=None)],
          'restarts':[],'pairs':[],'passing_network':[],'analyst_matches':[]}
    other=deepcopy(team);other.update(id='away',name='AWAY',players=[])
    return {'teams':[team,other],'match':{'home_id':'home'},'evidence':{}}


def collect(report):
    calls=[];drawings=[]
    def table(head,rows,widths=None,keep=False):calls.append(('table',head,list(rows)))
    def text(value,style=None):calls.append(('text',value))
    def new(value):calls.append(('page',value))
    _append_analysis_sections(report,drawings,text,table,new,lambda _:Drawing(495,335),
                              'Helvetica',['#b42338','#7037a7'],lambda x:'未提供' if x is None else str(x))
    return calls,drawings


def test_pdf_uses_route_denominators_and_preserves_unknown_xg():
    report=fixture();before=deepcopy(report);calls,drawings=collect(report)
    route=next(c for c in calls if c[0]=='table' and c[1][0]=='起点／传球次数')
    assert route[2][0]==['中场',10,2,20.0,.3]
    transition=next(c for c in calls if c[0]=='table' and c[1]==['方向','次数','xG有值','已记录xG'])
    assert transition[2][0]==['进攻',1,0,None]
    assert any(c[0]=='text' and '未选定双人组合' in c[1] for c in calls)
    assert report==before  # immutable snapshot not mutated by export
    assert len(drawings)>0


def test_pdf_exports_intervals_segments_and_newer_history_without_old_snapshot_fabrication():
    report=fixture();t=report['teams'][0]
    summary={'matches':4,'minutes':360,'xg_per90':.8,'xga_per90':None}
    t['report_extensions']={'history':{'matches':t['samples'],'recent5':summary,'recent10':summary},
        'matches':[{'match_id':'match1','segments':[{'label':'1H-0','minutes':15,'shots':2,
            'conceded':0,'xg':.3,'xga':None,'passes':20,'completed_passes':15,
            'long_passes':4,'duels':2,'duels_won':1}],
            'intervals':[{'period':1,'start_ms':0,'end_ms':900000,'minutes':15,'formation':'4231',
                'state':'drawing','own_players':11,'opponent_players':11,'shots':2,'xg':.3,
                'xga':None,'passes':20,'completed_passes':15}]}]}
    calls,_=collect(report)
    history=next(c for c in calls if c[0]=='table' and c[1][0]=='窗口')
    assert history[2][1]==['最近10场',4,360,.8,None]
    segments=next(c for c in calls if c[0]=='table' and c[1][0]=='比赛／阶段')
    assert segments[2][0][-3:]==[75.0,20.0,50.0]
    intervals=next(c for c in calls if c[0]=='table' and c[1][0]=='比赛／阶段／分钟')
    assert intervals[2][0][1]=='4231 / 平局 / 11v11'


def test_pdf_does_not_label_fouls_or_conflicting_outcomes_as_duel_wins():
    report=fixture()
    a={'team_id':'home','player_name':'A','outcome':1};b={'team_id':'away','player_name':'B','outcome':0}
    report['evidence']['contests']=[{'kind':'foul','a':a,'b':b},
        {'kind':'aerial','a':a,'b':dict(b,outcome=1)},
        {'kind':'take_on','a':a,'b':b},{'kind':'take_on','a':a,'b':dict(b,outcome=1)}]
    calls,_=collect(report)
    matrix=next(c for c in calls if c[0]=='table' and c[1][0]=='类型' and '主成功' in c[1])
    rows={r[0]:r for r in matrix[2]}
    assert rows['犯规关系'][-3:]==[0,0,1]
    assert rows['争顶'][-3:]==[0,0,1]
    assert rows['过人／防守对抗'][-3:]==[2,0,0]


def test_pdf_preserves_restart_missing_xg_and_historical_contest_counts():
    report=fixture();team=report['teams'][0]
    event={'match_id':'match1','event_id':'e1','period':1,'minute':12,'second':0,
        'direction':'attack','kind':'角球','x':100,'y':0,'end_x':92,'end_y':50,
        'xg':None,'shots':[{'event_id':'s1','xg':None}]}
    contest={'kind':'aerial','player_id':'a','player_name':'PLAYER_A',
        'opponent_id':'b','opponent_name':'PLAYER_B','contests':2,'won':1,'lost':0,'unknown':1}
    team['report_extensions']={'matches':[{'match_id':'match1','restarts':[event],
        'passes':[dict(event,kind='cross',outcome=1)],'contest_matrix':[contest]},
        {'match_id':'match2','contest_matrix':[contest]}]}
    calls,drawings=collect(report)
    restarts=next(c for c in calls if c[0]=='table' and c[1][0]=='类型' and '后续射门／已知xG' in c[1])
    assert restarts[2][0][-1]=='1 / 未提供'
    matrix=next(c for c in calls if c[0]=='table' and c[1][1]=='本队球员')
    assert matrix[2][0][-4:]==[4,2,0,2]
    assert any(c==('page','HOME · 传中路线') for c in calls)
    assert drawings


def test_pdf_timeline_preserves_period_clock_and_separates_simultaneous_labels():
    from reportlab.graphics.shapes import String,Circle
    report=fixture()
    report['evidence']['markers']=[{'period':1,'minute':10,'second':0,
        'team_id':'home','event_type':18 if i%2==0 else 19,'player_name':str(i)} for i in range(4)]
    _,drawings=collect(report);timeline=drawings[-1]
    labels=[x for x in timeline.contents if isinstance(x,String) and x.text in ('1','2','3','4')]
    assert len({(x.x,x.y) for x in labels})==4
    points=[x for x in timeline.contents if isinstance(x,Circle)]
    assert all(abs(x.cx-(25+445*10/45))<.01 for x in points)


def test_pdf_xa_ranking_preserves_coverage_and_shared_team_scale():
    from reportlab.graphics.shapes import Rect
    report=fixture();home=report['teams'][0];away=report['teams'][1]
    home['players'][0].update(xa=.2,xa_match_count=3)
    away['players']=[{'id':'b','name':'PLAYER_B','minutes':90,'xa':.4,'xa_match_count':1}]
    calls,drawings=collect(report)
    rankings=[c for c in calls if c[0]=='table' and c[1]==['球员','官方 xA','xA覆盖场次']]
    assert rankings[0][2]==[['PLAYER_A',.2,3]]
    assert rankings[1][2]==[['PLAYER_B',.4,1]]
    xa_drawings=[d for d in drawings if any(getattr(x,'text',None)=='已覆盖比赛的官方 xA 合计' for x in d.contents)]
    widths=[[x.width for x in d.contents if isinstance(x,Rect) and x.height==7 and x.width>7] for d in xa_drawings]
    assert widths==[[157.5],[315.0]]
