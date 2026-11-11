"""Shared, versioned chart data for the prematch report and PDF."""
from collections import Counter, defaultdict
from math import isfinite

VERSION = 'prematch-threat.v2'
THIRDS = ('后场', '中场', '前场')
LANES = ('右路', '右肋', '中路', '左肋', '左路')
KINDS = ('低位夺回', '中位夺回', '高位夺回', '开球', '角球', '任意球', '界外球', '门球', '未知')


def number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and isfinite(v)


def rate(value, minutes, units=90):
    return value * units / minutes if number(value) and number(minutes) and minutes > 0 else None


def zone(x, y):
    if not all(number(n) and 0 <= n <= 100 for n in (x, y)):
        return None
    return min(2, int(x * 3 / 100)) * 5 + min(4, int(y / 20))


def route(event):
    return zone(event.get('x'), event.get('y')), zone(event.get('end_x'), event.get('end_y'))


def label(z):
    return THIRDS[z // 5] + LANES[z % 5]


def route_label(key):
    return ' → '.join(map(label, key))


def signed(events):
    values = [e['pv_added'] for e in events]
    return dict(positive=sum(v for v in values if v > 0), negative=sum(v for v in values if v < 0),
                net=sum(values), count=len(values))


def values_per90(values, minutes):
    return {k: rate(values[k], minutes) for k in ('positive', 'negative', 'net')}


def action_kind(event):
    return {1: '传球', 2: '传球', 3: '过人'}.get(event.get('event_type'), '其他已评分动作')


def valid_pv(m):
    q = m.get('quality', {})
    return m.get('status') == 'available' and q.get('source_actions', 0) > 0 and not any(
        q.get(k) for k in ('unmatched_actions', 'conflicting_actions', 'missing_value_actions'))


def minutes_by_match(team):
    return {m['id']: m.get('minutes') for m in team.get('report_extensions', {}).get('history', {}).get('matches', [])}


def lineup_team(report, side):
    return next((row for row in (report.get('lineup') or {}).get('teams', []) if row.get('side') == side), {})


def full_match_xa_rate(player, matches):
    official = {m['match_id']: m.get('stats', {}) for m in player.get('official_by_match', [])}
    known = [(m['match_id'], a['total']) for m in matches for a in m.get('xa', [])
             if a.get('player_id') == player['id'] and number(a.get('total'))]
    if not known or any(not number(official.get(mid, {}).get('minsPlayed')) for mid, _ in known):
        return None
    return rate(sum(v for _, v in known), sum(official[mid]['minsPlayed'] for mid, _ in known))


def player_projection(team, pv_matches, events, report, side):
    grouped = defaultdict(list)
    for e in events:
        grouped[e.get('player_id')].append(e)
    players = {p['id']: p for p in team.get('players', [])}
    starters = {p.get('evidence_player_id') or (p.get('identity_link') or {}).get('evidence_player_id') or p.get('id')
                for p in lineup_team(report, side).get('players', [])}
    details = team.get('analyst_detail', {}).get('matches', [])
    results = []
    for pid in grouped.keys() | (starters & players.keys()):
        if not pid:
            continue
        player = players.get(pid, {})
        es = grouped[pid]
        if pv_matches and all('player_minutes' in m for m in pv_matches):
            minutes = sum(m['player_minutes'].get(pid, 0) for m in pv_matches)
        elif {m['match_id'] for m in pv_matches} == {m['id'] for m in team.get('samples', [])}:
            minutes = player.get('minutes')
        else:
            minutes = None
        if not es:
            minutes = None
        values = signed(es)
        categories = {kind: signed([e for e in es if action_kind(e) == kind])
                      for kind in ('传球', '过人', '其他已评分动作')}
        results.append(dict(id=pid, name=player.get('name', '身份未关联'), role=player.get('role'),
            starter=pid in starters, minutes=minutes, count=values['count'],
            per90=values_per90(values, minutes), per100=rate(values['net'], values['count'], 100),
            actions90=rate(values['count'], minutes), xg90=rate(player.get('xg'), player.get('minutes')),
            xa90=full_match_xa_rate(player, details),
            categories={k: values_per90(v, minutes) for k, v in categories.items()},
            distribution=sorted(e['pv_added'] for e in es)))
    return sorted(results, key=lambda p: (p['per90']['net'] is None, -(p['per90']['net'] or 0), p['id']))


def xt_projection(team, minute_map):
    matches = [m for m in team.get('analyst_detail', {}).get('matches', [])
               if m.get('coverage', {}).get('xt_status') == 'verified_source_revision']
    events = [e for m in matches for e in m.get('xt_actions', []) if e.get('team_id') == team['id']
              and number(e.get('xt_added')) and e.get('model_version')]
    versions = {e['model_version'] for e in events}
    if len(versions) != 1:
        return dict(status='mixed_versions' if versions else 'missing', routes=[], matches=0)
    mins = [minute_map.get(m['match_id']) for m in matches]
    minutes = sum(mins) if all(number(v) for v in mins) else None
    groups = defaultdict(list)
    unlocated = 0
    for e in events:
        a, b = route(e)
        if a is None or b is None:
            unlocated += 1
        elif a != b:
            groups[(a,b)].append(e)
    routes = []
    for key, es in groups.items():
        positive = sum(max(0,e['xt_added']) for e in es)
        if not positive:
            continue
        best = sorted(es, key=lambda e: (-e['xt_added'], e['event_id']))
        leaders = Counter(e.get('player_name') for e in es if e['xt_added'] > 0 and e.get('player_name'))
        positive_events = [e for e in es if e['xt_added'] > 0]
        def average_point(x, y):
            return [sum(e[k] for e in positive_events)/len(positive_events) for k in (x,y)]
        routes.append(dict(start=key[0], end=key[1], label=route_label(key), count=len(es),
            from_xy=average_point('x','y'), to_xy=average_point('end_x','end_y'),
            positive=positive, per90=rate(positive,minutes), average=sum(e['xt_added'] for e in es)/len(es),
            leaders=[p for p,_ in leaders.most_common(3)],
            leader_ids=list(dict.fromkeys(e.get('player_id') for e in best if e.get('player_id') and e.get('player_name') in dict(leaders.most_common(3)))),
            events=best))
    return dict(status='available', matches=len(matches), minutes=minutes, count=len(events),
                unlocated=unlocated, version=next(iter(versions)), training_cutoff_verified=False,
                routes=sorted(routes,key=lambda r:(-r['positive'],r['start'],r['end']))[:5])


def possessions(contexts, pv_index, pv_covered):
    groups = defaultdict(list)
    for mid, data in contexts.items():
        for p in data['possessions']:
            scores = [pv_index[mid,eid]['pv_added'] for eid in p['event_ids'] if (mid,eid) in pv_index]
            groups[p['kind']].append((p, sum(scores) if scores and mid in pv_covered else None))
    result = []
    for kind in KINDS:
        rows = groups[kind]
        if not rows:
            continue
        scores = [pv for _,pv in rows if pv is not None]
        entries = [p['entered_third'] for p,_ in rows if p['entered_third'] is not None]
        shots = [v for p,_ in rows for v in p['shots']]
        known = [x for x in shots if number(x)]
        result.append(dict(kind=kind, count=len(rows), pv_count=len(scores),
            average_pv=sum(scores)/len(scores) if scores else None,
            entry_count=sum(entries), entry_covered=len(entries),
            shots_count=sum(bool(p['shots']) for p,_ in rows), xg=sum(known) if known or not shots else None,
            known_xg=len(known), shots=len(shots)))
    return result


def best_chain(team, projection, pv_index):
    route_scores = {(e['match_id'], e['event_id']):e['xt_added']
                    for r in projection['routes'] for e in r['events']}
    xt = {(e['match_id'], e['event_id']): e['xt_added']
          for m in team.get('analyst_detail', {}).get('matches', [])
          for e in m.get('xt_actions', [])
          if e.get('model_version') == projection.get('version') and number(e.get('xt_added'))}
    candidates = []
    for c in team.get('chains', []):
        if c.get('direction') != 'attack' or not c.get('chain_complete') or not number(c.get('xg')):
            continue
        seq = [e for e in c.get('sequence', []) if e.get('team_id') == team['id']]
        if not any(route_scores.get((e['match_id'],e['event_id']),0) > 0 for e in seq):
            continue
        candidates.append((c, seq))
    if not candidates:
        return None
    c, seq = max(candidates,key=lambda x:(x[0]['xg'],x[0]['event_id']))
    events = []
    for e in seq:
        key = e['match_id'],e['event_id']
        events.append(dict(e, xt=xt.get(key), pv=pv_index.get(key,{}).get('pv_added'),
                           shot=e['event_id']==c['event_id']))
    return dict(shot={k:c.get(k) for k in ('match_id','event_id','minute','second','period','player_id','xg')},
                events=events, creator_linked=c.get('creator_linked',False), method=c.get('method'))


def matchup(attack, defence, routes, contexts, players, defence_minutes):
    if not routes:
        return None
    top = routes[0]
    key = (top['start'],top['end'])
    rows = [r for c in contexts.values() for r in c['conceded_routes'] if (r['start'],r['end']) == key]
    chains = [c for c in defence.get('chains',[]) if c.get('direction') == 'defence' and
              any(route(e) == key and e.get('team_id') not in (None,defence['id']) and e.get('success') == 1
                  and e.get('event_type') == 1 for e in c.get('sequence',[]))]
    known = [c['xg'] for c in chains if number(c.get('xg'))]
    alternative = [c for c in defence.get('chains',[]) if c.get('direction') == 'defence'
                   and number(c.get('xg')) and c not in chains]
    example = max(alternative,key=lambda c:c['xg'],default=None)
    present = [p['name'] for p in players if p['starter'] and p['id'] in top['leader_ids']]
    return dict(current_starters=present, defence_minutes=defence_minutes, route=top['label'], start=key[0], end=key[1], leaders=top['leaders'],
                count=top['count'], per90=top['per90'], defence_matches=len(contexts),
                conceded=sum(r['count'] for r in rows) if contexts else None,
                box_entries=sum(r['box_entries'] for r in rows) if contexts else None,
                shots=len(chains), known_xg=len(known), xg=sum(known) if known else None,
                events=[e for r in rows for e in r['events']][:6],
                counter={k:example.get(k) for k in ('match_id','event_id','period','minute','second','xg')} if example else None,
                defender=defence['name'], attacker=attack['name'])


def project(report):
    teams = report.get('teams', [])
    if len(teams) != 2:
        return None
    if not any(t.get('pv_analysis', {}).get('covered_matches') or
               any(m.get('xt_actions') for m in t.get('analyst_detail', {}).get('matches', [])) for t in teams):
        return None
    blocks, contexts = [], []
    for i, team in enumerate(teams):
        pv = team.get('pv_analysis', {})
        matches = pv.get('matches', [])
        context = {m['match_id']: m['threat_context'] for m in matches if 'threat_context' in m}
        contexts.append(context)
        covered = [m for m in matches if valid_pv(m)]
        minute_map = minutes_by_match(team)
        if not minute_map and len(covered) == len(team.get('samples',[])):
            minutes = pv.get('selected', {}).get('minutes')
        else:
            mins = [m.get('minutes',minute_map.get(m['match_id'])) for m in covered]
            minutes = sum(mins) if mins and all(number(v) for v in mins) else None
        events = [e for m in covered for e in m['events'] if number(e.get('pv_added'))]
        index = {(e['match_id'],e['event_id']): e for e in events}
        thirds = []
        for i_third, name in enumerate(THIRDS):
            group = signed([e for e in events if e.get('zone') is not None and e['zone']//5==i_third])
            thirds.append(dict(name=name, count=group['count'], **values_per90(group,minutes)))
        xt = xt_projection(team, minute_map)
        side = ('home','away')[i]
        changes = next((c for c in report.get('lineup_changes',{}).get('teams',[]) if c.get('side')==side),{})
        blocks.append(dict(id=team['id'], name=team['name'], matches=len(covered), requested=len(team.get('samples',[])),
            minutes=minutes, totals=signed(events), per90=values_per90(signed(events),minutes),
            unlocated=sum(e.get('zone') is None for e in events), thirds=thirds,
            players=player_projection(team,covered,events,report,side),
            lineup_change=changes, xt=xt, possession_matches=len(context),
            possessions=possessions(context,index,{m['match_id'] for m in covered}),
            chain=best_chain(team,xt,index)))
    if len({b['xt'].get('version') for b in blocks if b['xt'].get('version')}) > 1:
        for b in blocks:
            b['xt'] = dict(b['xt'], status='mixed_versions', routes=[])
            b['chain'] = None
    for i,b in enumerate(blocks):
        opponent_minutes = minutes_by_match(teams[1-i])
        covered_minutes = [opponent_minutes.get(mid) for mid in contexts[1-i]]
        defence_minutes = sum(covered_minutes) if covered_minutes and all(number(v) for v in covered_minutes) else None
        b['matchup'] = matchup(teams[i],teams[1-i],b['xt']['routes'],contexts[1-i],b['players'],defence_minutes)
    return dict(version=VERSION, teams=blocks)
