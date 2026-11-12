"""Project the six-page reader from the saved report, without querying a live feed."""
from collections import defaultdict

from .analyst_threat import KINDS, label, number, project, rate, signed, valid_pv, values_per90, zone


def grid(events, minutes, *, mirror=False, destination=False):
    values = [0.] * 15
    counts = [0] * 15
    for event in events:
        prefix = 'end_' if destination else ''
        x, y = event.get(prefix + 'x'), event.get(prefix + 'y')
        if mirror and number(x) and number(y):
            x, y = 100 - x, 100 - y
        idx = zone(x, y)
        if idx is not None:
            counts[idx] += 1
            values[idx] += rate(max(0, event['pv_added']), minutes)
    return {'values': values, 'counts': counts}


def possession_rows(matches, by_event):
    grouped = defaultdict(list)
    assigned = set()
    for match in matches:
        for chain in match.get('threat_context', {}).get('possessions', []):
            keys = {(match['match_id'], event_id) for event_id in chain['event_ids']} & by_event.keys()
            assigned.update(keys)
            scores = signed([by_event[key] for key in keys]) if keys else None
            grouped[chain['kind']].append((chain, scores))
    positive = sum(scores['positive'] for rows in grouped.values() for _, scores in rows if scores)
    result = []
    for kind in KINDS:
        rows = grouped[kind]
        if not rows:
            continue
        scored = [scores for _, scores in rows if scores]
        entries = [chain['entered_third'] for chain, _ in rows if chain['entered_third'] is not None]
        entry_scores = [(chain['entered_third'], scores['positive']) for chain, scores in rows
                        if chain['entered_third'] is not None and scores]
        total_entry_pv = sum(pv for _, pv in entry_scores)
        result.append(dict(
            kind=kind, count=len(rows), pv_count=len(scored),
            average_pv=sum(scores['net'] for scores in scored) / len(scored) if scored else None,
            positive_share=sum(scores['positive'] for scores in scored) / positive if positive else None,
            entry_count=sum(entries), entry_covered=len(entries),
            weighted_entry=sum(pv for entered, pv in entry_scores if entered) / total_entry_pv if total_entry_pv else None,
            shots_count=sum(bool(chain['shots']) for chain, _ in rows),
        ))
    return result, len(by_event) - len(assigned)


def player_rows(team, block, events):
    positions = {player['id']: player.get('position') for player in team['players']}
    grouped = defaultdict(list)
    for event in events:
        grouped[event.get('player_id')].append(event)
    result = []
    for player in block['players']:
        minutes = player.get('minutes')
        if minutes is None or minutes < 180:
            continue
        actions = grouped[player['id']]
        passes = [event for event in actions if event['event_type'] in (1, 2)]
        failed = [event for event in passes if event['success'] == 0]
        own_half = [event for event in passes if number(event.get('x')) and event['x'] < 50]
        own_half_failed = [event for event in own_half if event['success'] == 0]
        zones = grid(actions, minutes)
        top_zone = max(range(15), key=zones['values'].__getitem__)
        positive = player['per90']['positive']
        result.append(dict(
            player, position=positions.get(player['id']), zones=zones, zone_label=label(top_zone),
            zone_share=zones['values'][top_zone] / positive if positive else 0,
            failed_loss90=rate(-signed(failed)['negative'], minutes),
            half_attempts=len(own_half), half_failures=len(own_half_failed),
            half_failure_rate=len(own_half_failed) / len(own_half) if own_half else None,
            half_loss90=rate(-signed(own_half_failed)['negative'], minutes),
        ))
    return result


def paired_pv_matches(team):
    matches = team.get('pv_analysis', {}).get('matches', [])
    samples = team.get('samples', [])
    if len(matches) != 5 or len(samples) != 5 or {m['match_id'] for m in matches} != {m['id'] for m in samples}:
        return []
    return [
        match for match in matches
        if valid_pv(match)
        and valid_pv(dict(match.get('against') or {}, status='available'))
        and number(match.get('minutes')) and match['minutes'] > 0
        and match.get('threat_context')
    ]


def reader_data(report):
    teams = report.get('teams', [])
    filters = report.get('filters', {})
    if len(teams) != 2:
        return None
    if any(str(filters.get(key)) != value for key, value in
           {'window': '5', 'eleven': '1', 'state': 'all', 'venue': 'all'}.items()):
        return None
    if any(filters.get(key) for key in ('since', 'opponent', 'pair_a', 'pair_b')):
        return None
    comparable = []
    for team in teams:
        matches = paired_pv_matches(team)
        if not matches:
            return None
        covered_ids = {match['match_id'] for match in matches}
        pv = team['pv_analysis']
        selected = dict(pv.get('selected', {}), minutes=sum(match['minutes'] for match in matches))
        comparable.append(dict(
            team, pv_analysis=dict(pv, matches=matches, selected=selected, covered_matches=len(matches)),
            chains=[chain for chain in team.get('chains', []) if chain.get('match_id') in covered_ids],
        ))
    projection = project(dict(report, teams=comparable))
    result = []
    for team, block in zip(comparable, projection['teams']):
        matches = team['pv_analysis']['matches']
        minutes = block['minutes']
        events = [event for match in matches for event in match['events']]
        against = [event for match in matches for event in match['against']['events']]
        delivery = [event for event in against if event['event_type'] in (1, 2)
                    and event['success'] == 1 and event['pv_added'] > 0]
        by_event = {(event['match_id'], event['event_id']): event for event in events}
        possessions, unassigned = possession_rows(matches, by_event)
        result.append(dict(
            block, samples=team['samples'], routes=[r for r in block['xt']['routes'] if r['per90'] is not None],
            players=player_rows(team, block, events),
            player_names={p['id']: p['name'] for p in team['players']},
            attack_grid=grid(events, minutes), against_grid=grid(against, minutes, mirror=True),
            against_attacking_grid=grid(against, minutes),
            against_delivery_grid=grid(delivery, minutes, mirror=True, destination=True),
            against_per90=values_per90(signed(against), minutes), against_count=len(against),
            possessions=possessions, chains_total=sum(p['count'] for p in possessions),
            unassigned_pv_actions=unassigned,
        ))
    return {'match': report['match'], 'teams': result}
