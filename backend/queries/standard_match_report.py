"""Compose standard statistics and existing presentation from one read snapshot.

No raw metric dictionary is returned. Private source join references preserve
distinct unresolved players without publishing them as canonical identities.
"""
from contextlib import closing

from backend.queries.match_report import match_report
from backend.vendor.miaodi_standard.facts import read, match_source, canonical_id, team_statistics
from backend.vendor.miaodi_standard.identity_lookup import resolve
from backend.vendor.miaodi_standard.records import player_statistics
from backend.vendor.miaodi_standard.metrics import UNVERIFIED_DEFINITIONS, definition_for

VERSION = 'miaodi-website-report.v1'
UNVERIFIED_CODES = {(provider, scope, definition_for(provider, key, scope)[0])
                    for provider, scope, key in UNVERIFIED_DEFINITIONS}


def standard_match_report(registry, source, provider, identity, *, canonical_references=False):
    if provider != 'fotmob':
        raise ValueError('unsupported_website_series')
    with closing(read(registry)) as ids, closing(read(source)) as data:
        identity = resolve(ids, identity)
        sid = int(match_source(ids, identity, provider))
        presentation = match_report(data, sid)
        base = {'schema_version': VERSION, 'match_id': identity}
        if presentation is None:
            return {**base, 'schema_version': 'miaodi-website-report.v2' if canonical_references else VERSION,
                    'available': False, 'reason': '暂无比赛报告'}

        team_refs, player_refs = [], []
        team = team_statistics(registry, source, provider, identity,
                               _connections=(ids, data), _source_refs=team_refs)
        player = player_statistics(registry, source, provider, identity,
                                   _connections=(ids, data), _source_refs=player_refs)
        definitions = {}
        team_rows = {}
        periods = {'All': 'full_time', 'FirstHalf': 'first_half', 'SecondHalf': 'second_half'}
        for row in presentation['team_stats'] + presentation['team_stats_by_half']:
            key = (str(row['team_id']), periods[row['period']])
            team_rows[key] = {k: row[k] for k in ('team_id', 'is_home')}
            team_rows[key].update(period=key[1], canonical_team_id=canonical_id(ids, 'team', provider, key[0]))
        player_rows = {}
        for row in presentation['player_stats']:
            key = (str(row['team_id']), row['player_id'])
            player_rows[key] = {k: row[k] for k in ('team_id', 'player_id', 'name', 'is_home', 'is_goalkeeper', 'rating')}
            player_rows[key].update(canonical_team_id=canonical_id(ids, 'team', provider, key[0]),
                                    canonical_player_id=canonical_id(ids, 'player', provider, key[1]))
        for scope, projection, refs, rows in [('team', team, team_refs, team_rows),
                                              ('player', player, player_refs, player_rows)]:
            if len(refs) != len(projection['observations']):
                raise ValueError('statistics_join_length_mismatch')
            seen = set()
            for ref, metric in zip(refs, projection['observations']):
                if ref not in rows:
                    raise ValueError('statistics_presentation_join_missing')
                code = metric['metric_code']
                grain = (*ref, code)
                if grain in seen:
                    raise ValueError('statistics_presentation_collision')
                seen.add(grain)
                rows[ref][code] = metric['value']
                definition = {'entity_scope': scope, 'metric_code': code, 'unit': metric['unit'],
                              'definition_version': metric['definition_version'],
                              'definition_status': 'unverified' if (provider, scope, code) in UNVERIFIED_CODES else 'mapped'}
                if (scope, code) in definitions and definitions[scope, code] != definition:
                    raise ValueError('statistics_definition_collision')
                definitions[scope, code] = definition

        presentation.update(team_stats=[v for k, v in team_rows.items() if k[1] == 'full_time'],
                            team_stats_by_half=[v for k, v in team_rows.items() if k[1] != 'full_time'],
                            player_stats=list(player_rows.values()),
                            metric_definitions=[definitions[k] for k in sorted(definitions)],
                            statistics_quality={'team': team['quality'], 'player': player['quality']})
        result = {**base, 'available': True, **presentation}
        if canonical_references:
            from .report_identity import canonicalize_report
            return canonicalize_report(result, ids, provider)
        return result


def standard_match_report_v2(registry, source, provider, identity):
    return standard_match_report(registry, source, provider, identity, canonical_references=True)
