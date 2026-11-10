from backend.studio.analyst_pdf_sections import formations,official_rate,player_count,player_scope

def test_player_leaderboard_scopes_do_not_mix_goals_and_selected_shots():
    assert '所选时段' in player_scope('shots') and '不含点球' in player_scope('shots')
    assert player_scope('progressive')=='所选时段'
    assert all('整场' in player_scope(k) for k in ('goals','assists','key_passes','totalCross','aerialWon','tackles','interceptions'))

def test_historical_opponent_uses_opposite_side_without_mutating_snapshot():
    from backend.studio.analyst_pdf_layout import sequence_for_pitch
    events=[{'team_id':'chelsea'},{'team_id':'bournemouth'},{'team_id':None}]
    result=sequence_for_pitch(events,'chelsea',1)
    assert [e['_display_side'] for e in result]==[1,0,None]
    assert all('_display_side' not in e for e in events)


def test_formations_sum_disjoint_intervals_and_preserve_missing_xg():
    interval=dict(formation='4231',minutes=15,shots=2,conceded=1,known_xg=1,known_xga=0,xg=.3,xga=None)
    team={'report_extensions':{'matches':[{'match_id':'a','intervals':[interval,dict(interval,minutes=10)]},{'match_id':'b','intervals':[interval]}]}}
    x=formations(team)[0]
    assert x['matches']==2 and x['minutes']==40 and x['shots']==6 and x['known_xg']==3
    assert abs(x['xg']-.9)<1e-8 and x['xga'] is None


def test_success_rate_uses_paired_match_denominators_not_average_percentages():
    t={'analyst_matches':[{'official':{'accuratePass':1,'totalPass':2}},{'official':{'accuratePass':9,'totalPass':10}},{'official':{'totalPass':100}}]}
    value,coverage=official_rate(t,'accuratePass','totalPass')
    assert abs(value-1000/12)<1e-8 and coverage==2


def test_sparse_player_stats_are_unknown_and_explicit_role_zero_is_real():
    p={'id':'p','official_by_match':[{'stats':{}},{'stats':{'totalCross':0}}]}
    assert player_count({},p,'totalCross')==(0,1)
    assert player_count({},p,'totalYellowCard')==(None,0)
    t={'analyst_detail':{'matches':[{'roles':[{'player_id':'p','assists':0}]}]}}
    assert player_count(t,p,'assists')==(0,1)
