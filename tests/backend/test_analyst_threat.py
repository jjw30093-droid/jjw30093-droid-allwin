import pytest

from backend.studio.analyst_threat import possessions, rate, route, signed, xt_projection
from backend.studio.analyst_threat_reader import possession_rows, reader_data


def test_official_zero_and_missing_minutes_are_distinct():
    assert rate(0, 45) == 0
    assert rate(0, 0) is None
    assert rate(None, 45) is None
    assert signed([{'pv_added': .2}, {'pv_added': -.3}])['net'] == pytest.approx(-.1)


def test_x_t_does_not_mix_models():
    def event(version):
        return dict(team_id='A', model_version=version, xt_added=.1)
    m = dict(match_id='M', coverage=dict(xt_status='verified_source_revision'), xt_actions=[event('v1'),event('v2')])
    assert xt_projection(dict(id='A', analyst_detail=dict(matches=[m])), {'M':90})['status'] == 'mixed_versions'
    assert route(dict(x=100,y=100,end_x=0,end_y=0)) == (14,0)


def test_possession_rates_only_use_eligible_denominators():
    contexts = {'M': {'possessions': [
        dict(kind='高位夺回', event_ids=['1'], entered_third=None, shots=[.2]),
        dict(kind='高位夺回', event_ids=['2'], entered_third=False, shots=[]),
        dict(kind='高位夺回', event_ids=['3'], entered_third=True, shots=[None]),
    ]}}
    p, = possessions(contexts,{('M','1'):{'pv_added':0}}, {'M'})
    assert (p['entry_count'],p['entry_covered'],p['shots_count'],p['count']) == (1,2,2,3)
    assert (p['pv_count'],p['average_pv'],p['known_xg'],p['shots']) == (1,0,1,2)


def test_reader_weights_pv_only_over_eligible_scored_possessions():
    matches = [{'match_id': 'M', 'threat_context': {'possessions': [
        dict(kind='中位夺回', event_ids=['1'], entered_third=True, shots=[.2]),
        dict(kind='中位夺回', event_ids=['2'], entered_third=False, shots=[]),
        dict(kind='中位夺回', event_ids=['3'], entered_third=None, shots=[]),
        dict(kind='中位夺回', event_ids=['4'], entered_third=True, shots=[]),
    ]}}]
    events = {('M', event): {'pv_added': pv} for event, pv in [('1', .2), ('2', .1), ('3', .7), ('5', .2)]}
    [row], unassigned = possession_rows(matches, events)
    assert (row['entry_count'], row['entry_covered'], row['pv_count'], unassigned) == (2, 3, 3, 1)
    assert row['weighted_entry'] == pytest.approx(2 / 3)
    assert row['average_pv'] == pytest.approx(1 / 3)


def test_legacy_snapshot_without_opponent_pv_keeps_existing_reader():
    matches = [dict(match_id=str(i), status='available', minutes=90,
                    quality={'source_actions': 1}, threat_context={'possessions': []}) for i in range(5)]
    team = dict(samples=[{'id': m['match_id']} for m in matches], pv_analysis={'matches': matches})
    assert reader_data(dict(teams=[team, team], filters=dict(window='5', eleven='1', state='all', venue='all'))) is None



def test_reader_uses_same_covered_minutes_for_own_and_opponent_pv():
    def team(tid, missing_opponent=False):
        matches = []
        for i in range(5):
            mid = f'{tid}{i}'
            quality = {'source_actions': 1}
            event = dict(match_id=mid, event_id='1', x=25, y=25, zone=1,
                         pv_added=.1, event_type=1, success=1)
            matches.append(dict(match_id=mid, minutes=90, status='available', quality=quality,
                                events=[event], threat_context={'possessions': [], 'conceded_routes': []},
                                against={} if i == 4 and missing_opponent else {'events': [event], 'quality': quality}))
        return dict(id=tid, name=tid, players=[], samples=[{'id': m['match_id']} for m in matches],
                    pv_analysis={'matches': matches, 'covered_matches': 5})

    a, b = team('A'), team('B', True)
    filters = dict(window='5', eleven='1', state='all', venue='all')
    result = reader_data(dict(match={}, teams=[a,b], filters=filters))
    home, away = result['teams']
    assert (home['matches'], away['matches'], away['requested']) == (5,4,5)
    assert (home['minutes'], away['minutes']) == (450,360)
    assert sum(away['attack_grid']['counts']) == away['against_count'] == 4
    assert away['against_per90']['positive'] == pytest.approx(.1)
    assert len(b['pv_analysis']['matches']) == 5
