"""Readonly, explicit season view adapters over existing facts and silver data."""
from backend.vendor.miaodi_standard.contract import stable_id, number
from backend.vendor.miaodi_standard.facts import identity_lookup, snapshots
from backend.vendor.miaodi_standard.identity_lookup import resolve
from backend.vendor.miaodi_standard.season_metrics import season_observation
from backend.queries import league_stats, player_quadrant
from backend.queries.leagues import LEAGUE_META, display_rank
from backend.queries.presentation_text import display_name
from backend.metrics.registry import get_metric
from backend.queries.player_ratio_definitions import PAIRED_PASS_INPUTS, LEGACY_RATIO_VERSION, PAIRED_RATIO_VERSION
from backend.silver.player_season import pass_completion_eligible


def competition_catalog(registry, source):
    with snapshots(registry, source) as (ids, _):
        lookup = identity_lookup(ids, 'fotmob')
        result = []
        for lid, meta in sorted(LEAGUE_META.items(), key=lambda x: display_rank(x[0])):
            identity = lookup('competition', lid)
            if identity:
                result.append({'competition_id': identity, 'code': meta['code'],
                               'name': meta['name_zh'], 'name_en': meta['name_en'],
                               'website_path': f'/league/{lid}'})
        return result


def _name(value, source, fallback):
    return display_name(value, source, fallback)


def _player(row, lookup, record_id):
    source = row['player_id']; identity = lookup('player', source)
    return {'record_id': record_id, 'player_id': identity,
            'name': _name(row['name'], source, '姓名未收录'), 'name_en': _name(row.get('name_en'), source, None),
            'avatar_url': f'/api/v1/media/player-avatars/{identity}.png' if identity else None}


def _team(row, lookup):
    return {'team_id': lookup('team', row['team_id']),
            'name': _name(row['name'], row['team_id'], '球队名称未收录'),
            'name_en': _name(row.get('name_en'), row['team_id'], None), 'crest_url': row.get('crest_url')}


def _identity_issues(player, team):
    return [{'code': 'unresolved_identity', 'field': field}
            for field, value in [('player_id', player['player_id']), ('team_id', team['team_id'])]
            if value is None]


def _standard_ratios(data, league_id, season):
    ratios = {}; issues = {}; seen = set()
    rows = data.execute('''SELECT Player_ID,metric_key,numerator_sum,denominator_sum,paired_matches,methodology_version
                           FROM silver_player_season_ratios WHERE League_ID=? AND Season=?''',
                        (league_id, season)).fetchall()
    for row in rows:
        pid = str(row['Player_ID']); key = row['metric_key']; grain = (pid, key)
        if grain in seen:raise ValueError('duplicate_player_ratio')
        seen.add(grain)
        def issue(code):issues.setdefault(pid, []).append({'code': code, 'field': 'ratios'})
        if key not in player_quadrant.RATIO_METRIC_KEYS:
            issue('unmapped_derived_metric'); continue
        if key in PAIRED_PASS_INPUTS:continue # Recompute on matched raw observations, never mix independent sums.
        definition = get_metric(key)
        if row['methodology_version'] != 'v1' or definition.methodology_version != 'v1':
            ratios.setdefault(pid, {})[key] = None
            issue('unverified_derived_definition'); continue
        try:
            low = None if key in ('finishing_delta_per90', 'goals_prevented_per90') else 0
            numerator = number(row['numerator_sum'], low=low)
            denominator = number(row['denominator_sum'], low=0)
            paired = number(row['paired_matches'], 'count', 0)
            value = number(definition.display_scale * numerator / denominator) if numerator is not None and denominator is not None and denominator > 0 else None
            if definition.unit == '%' and value is not None and not 0 <= value <= 100:
                raise ValueError('derived_percentage_out_of_range')
        except ValueError:
            ratios.setdefault(pid, {})[key] = None; issue('invalid_derived_metric_value'); continue
        if paired is None:issue('missing_pairing_count')
        ratios.setdefault(pid, {})[key] = {'value': value, 'numerator': numerator,
                                         'denominator': denominator, 'input_matches': paired,
                                         'definition_version': LEGACY_RATIO_VERSION,
                                         'sample_policy': 'independent_available_sums'}
    if season and pass_completion_eligible(season):
        for key, column in PAIRED_PASS_INPUTS.items():
            # Identifiers come only from the fixed reviewed mapping above.
            numerator = 'p.'+column; denominator = 'p.accurate_passes_total'
            reported = f'{numerator} IS NOT NULL AND {denominator} IS NOT NULL'
            valid = (f"{reported} AND typeof({numerator}) IN ('integer','real') AND typeof({denominator}) IN ('integer','real') "
                     f'AND {numerator}>=0 AND {denominator}>=0 AND {numerator}=CAST({numerator} AS INTEGER) '
                     f'AND {denominator}=CAST({denominator} AS INTEGER) AND {numerator}<={denominator}')
            raw = data.execute(f'''SELECT p.Player_ID,COUNT(*) AS source_matches,
                SUM(CASE WHEN {valid} THEN {numerator} END) AS numerator,
                SUM(CASE WHEN {valid} THEN {denominator} END) AS denominator,
                SUM(CASE WHEN {valid} THEN 1 ELSE 0 END) AS input_matches,
                SUM(CASE WHEN {reported} AND NOT({valid}) THEN 1 ELSE 0 END) AS invalid_inputs
                FROM fact_player_match_stats p JOIN dim_match dm ON dm.Match_ID=p.Match_ID
                WHERE dm.status='Finish' AND dm.League_ID=? AND dm.Season=?
                GROUP BY p.Player_ID LIMIT 5001''', (league_id, season)).fetchall()
            if len(raw)>5000:raise ValueError('player_passing_limit')
            for r in raw:
                pid=str(r['Player_ID'])
                n=number(r['numerator'],'count',0); den=number(r['denominator'],'count',0)
                value=100*n/den if n is not None and den is not None and den>0 else None
                ratios.setdefault(pid,{})[key]={'value':value,'numerator':n,'denominator':den,
                    'input_matches':r['input_matches'],'definition_version':PAIRED_RATIO_VERSION,
                    'sample_policy':'same_match_observed_inputs'}
                if r['input_matches']<r['source_matches']:
                    issues.setdefault(pid,[]).append({'code':'incomplete_paired_inputs','field':'ratios'})
                if r['invalid_inputs']:
                    issues.setdefault(pid,[]).append({'code':'invalid_derived_metric_value','field':'ratios'})
    return ratios, issues


def player_season_view(registry, source, competition_id, league_id, season, kind):
    with snapshots(registry, source) as (ids, data):
        competition_id = resolve(ids, competition_id)
        lookup = identity_lookup(ids, 'fotmob')
        # Caller also authorizes this source competition; reject an accidental
        # cross-competition projection even if this function is used elsewhere.
        if lookup('competition', league_id) != competition_id:
            raise ValueError('competition_identity_mismatch')
        raw = (league_stats.player_leaderboards(data, league_id, season, source_metadata=True)
               if kind == 'players' else player_quadrant.player_quadrant_stats(data, league_id, season, include_ratios=False))
        base = {'competition_id': competition_id, 'season': raw['season'],
                'available_seasons': raw['available_seasons'], 'empty_reason': raw.get('empty_reason')}
        if kind == 'players':
            boards = []
            for board in raw['boards']:
                definition = season_observation('player', board['stat_name'], None)
                if definition is None:
                    raise ValueError('unregistered_website_board')
                entries = []
                for entry in board['entries']:
                    record = stable_id('record', competition_id, f"player-board:{raw['season']}:{board['stat_name']}:{entry['player_id']}")
                    player = _player(entry, lookup, record); team = _team(entry['team'], lookup)
                    issues = _identity_issues(player, team)
                    definition_issues = []
                    try:
                        metric = season_observation('player', board['stat_name'], entry['value'], entry['_source_metadata'], issues=definition_issues)
                    except ValueError:
                        metric = season_observation('player', board['stat_name'], None, entry['_source_metadata'])
                        issues.append({'code': 'invalid_metric_value', 'field': 'value'})
                    issues.extend({'code': code, 'field': 'aggregation'} for code in definition_issues)
                    if metric['aggregation'] == 'unverified':
                        issues.append({'code': 'unverified_aggregation', 'field': 'aggregation'})
                    if metric['unit'].startswith('unverified'):
                        issues.append({'code': 'unverified_unit', 'field': 'unit'})
                    entries.append({**player, 'team': team, 'rank': entry['rank'],
                                    **{k: metric[k] for k in ('value', 'unit', 'aggregation', 'definition_version')},
                                    'team_color': entry['team_color'], 'issues': issues})
                boards.append({'metric_code': definition['metric_code'],
                               'series_id': stable_id('series', 'fotmob', 'season-statistics:player:'+board['stat_name']),
                               'label_zh': board['label_zh'], 'direction': board['direction'], 'entries': entries})
            return {**base, 'schema_version': 'miaodi-website-player-boards.v1', 'boards': boards}
        if kind != 'player-quadrant':
            raise ValueError('unknown_player_season_view')
        ratios, ratio_issues = _standard_ratios(data, league_id, raw['season'])
        rows = []
        for row in raw['rows']:
            record = stable_id('record', competition_id, f"player-quadrant:{raw['season']}:{row['player']['player_id']}")
            player = _player(row['player'], lookup, record); team = _team(row['team'], lookup)
            rows.append({'player': player, 'team': team,
                         'issues': _identity_issues(player, team) + ratio_issues.get(str(row['player']['player_id']), []),
                         'ratios': ratios.get(str(row['player']['player_id'])),
                         **{k: row.get(k) for k in ('team_color', 'usual_position', 'appearances', 'minutes_played',
                                                   'team_minutes', 'minutes_share', 'teams_count')}})
        return {**base, 'schema_version': 'miaodi-website-player-quadrant.v2',
                'definition_version': PAIRED_RATIO_VERSION, 'rows': rows,
                'excluded_below_floor': raw.get('excluded_below_floor', 0)}
