"""Canonicalize report references inside its existing registry read snapshot."""
import re

from backend.vendor.miaodi_standard.contract import source_id, stable_id
from backend.vendor.miaodi_standard.facts import identity_lookup
from backend.queries.presentation_text import display_name


def canonicalize_report(report, ids, provider):
    report['schema_version'] = 'miaodi-website-report.v2'
    if not report['available']:
        return report
    lookup = identity_lookup(ids, provider)
    mid = report['match_id']

    def participant(raw, location, *, shot=False):
        try:
            raw = source_id(raw)
        except ValueError:
            return {'player_id': None, 'participant_id': None if shot else stable_id('report_record', 'miaodi', mid+'/'+location),
                    **({} if shot else {'participant_scope': 'report_record'}), 'avatar_url': None}
        pid = lookup('player', raw)
        return {'player_id': pid,
                'participant_id': stable_id('match_participant', 'miaodi', mid+'/'+provider+'/'+raw),
                **({} if shot else {'participant_scope': 'match_participant'}),
                'avatar_url': f'/api/v1/media/player-avatars/{pid}.png' if pid and re.fullmatch(r'[1-9][0-9]{0,18}', raw) else None}

    def person(row, location, *, shot=False):
        raw = row['player_id']
        for key in ('name', 'player_name'):
            if key in row:row[key] = display_name(row[key], raw, '未知球员')
        if 'name_en' in row:row['name_en'] = display_name(row['name_en'], raw, None)
        row.update(participant(raw, location, shot=shot))
        if 'team_id' in row:row['team_id'] = lookup('team', row['team_id'])
        row.pop('canonical_player_id', None);row.pop('canonical_team_id', None)

    for ti, team in enumerate(report['lineups']):
        team['team_id'] = lookup('team', team['team_id'])
        for section in ('starters', 'bench'):
            for i, row in enumerate(team[section]):person(row, f'lineup/{ti}/{section}/{i}')
    for section in ('team_stats', 'team_stats_by_half'):
        for row in report[section]:
            row['team_id'] = row.pop('canonical_team_id')
    for i, row in enumerate(report['player_stats']):person(row, f'player_stats/{i}')
    for i, row in enumerate(report['shots']):
        person(row, f'shots/{i}', shot=True);row.pop('shot_id', None)
    if report['top_rated']:person(report['top_rated'], 'top_rated')
    return report
