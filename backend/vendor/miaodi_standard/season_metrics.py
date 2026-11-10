"""Season boards have independent definitions from single-match statistics.

Internal source keys map explicitly. A field name alone cannot establish the
denominator; retain an unresolved basis when source metadata cannot prove it.
Never round a rate to a count, infer a currency, or export source metadata.
"""
import json
from .contract import number

# code, base unit, minimum, maximum. Denominator is established separately.
TEAM = {
 '_set_piece_goals_conceded_team':('set_piece_goals_conceded','count',0,None),
 '_set_piece_goals_team':('set_piece_goals','count',0,None),
 '_xg_diff_team':('expected_goals_difference','expected_count',None,None),
 'accurate_cross_team':('crosses_completed','count',0,None),
 'accurate_long_balls_team':('long_passes_completed','count',0,None),
 'accurate_pass_team':('passes_completed','count',0,None),
 'big_chance_missed_team':('big_chances_missed','count',0,None),
 'big_chance_team':('big_chances','count',0,None),
 'clean_sheet_team':('clean_sheets','count',0,None),
 'corner_taken_team':('corners_taken','count',0,None),
 'effective_clearance_team':('clearances','count',0,None),
 'expected_goals_conceded_team':('expected_goals_against','expected_count',0,None),
 'expected_goals_team':('expected_goals','expected_count',0,None),
 'fk_foul_lost_team':('fouls_committed','count',0,None),
 'goals_conceded_team_match':('goals_conceded','count',0,None),
 'goals_team_match':('goals','count',0,None),
 'home_attendance_team':('home_attendance','people',0,None),
 'interception_team':('interceptions','count',0,None),
 'ontarget_scoring_att_team':('shots_on_target','count',0,None),
 'penalty_conceded_team':('penalties_conceded','count',0,None),
 'penalty_won_team':('penalties_won','count',0,None),
 'phys_tdc_team':('distance_covered','unverified',0,None),
 'poss_won_att_3rd_team':('possession_won_attacking_third','count',0,None),
 'possession_percentage_team':('possession_percent','percent',0,100),
 'rating_team':('team_rating','rating_points',0,10),
 'saves_team':('saves','count',0,None),
 'squad_value_team':('squad_value','unverified_currency',0,None),
 'total_red_card_team':('red_cards','count',0,None),
 'total_tackle_team':('tackles','count',0,None),
 'total_yel_card_team':('yellow_cards','count',0,None),
 'touches_in_opp_box_team':('touches_in_opposition_box','count',0,None),
}
PLAYER = {
 '_expected_goals_and_expected_assists_per_90':('expected_goals_plus_assists','expected_count',0,None),
 '_goals_and_goal_assist':('goals_plus_assists','count',0,None),
 '_goals_prevented':('goals_prevented','expected_count',None,None),
 '_save_percentage':('save_percent','percent',0,100),
 'accurate_long_balls':('long_passes_completed','count',0,None),
 'accurate_pass':('passes_completed','count',0,None),
 'ball_recovery':('ball_recoveries','count',0,None),
 'big_chance_created':('big_chances_created','count',0,None),
 'big_chance_missed':('big_chances_missed','count',0,None),
 'clean_sheet':('clean_sheets','count',0,None),
 'defensive_contributions':('defensive_contributions','unverified',0,None),
 'effective_clearance':('clearances','count',0,None),
 'expected_assists':('expected_assists','expected_count',0,None),
 'expected_assists_per_90':('expected_assists','expected_count',0,None),
 'expected_goals':('expected_goals','expected_count',0,None),
 'expected_goals_per_90':('expected_goals','expected_count',0,None),
 'expected_goalsontarget':('expected_goals_on_target','expected_count',0,None),
 'fouls':('fouls_committed','count',0,None),
 'goal_assist':('goal_assists','count',0,None),
 'goals':('goals','count',0,None),
 'goals_conceded':('goals_conceded','count',0,None),
 'goals_per_90':('goals','count',0,None),
 'interception':('interceptions','count',0,None),
 'lbp_total':('line_breaking_passes','count',0,None),
 'mins_played':('minutes_played','minutes',0,None),
 'ontarget_scoring_att':('shots_on_target','count',0,None),
 'outfielder_block':('shots_blocked_defensively','count',0,None),
 'penalty_conceded':('penalties_conceded','count',0,None),
 'penalty_save':('penalties_saved','count',0,None),
 'penalty_won':('penalties_won','count',0,None),
 'phys_sprints':('sprints','count',0,None),
 'phys_sprints_per_90':('sprints','count',0,None),
 'phys_tdc':('distance_covered','unverified',0,None),
 'phys_tdc_per_90':('distance_covered','unverified',0,None),
 'phys_ts':('top_speed','unverified',0,None),
 'poss_won_att_3rd':('possession_won_attacking_third','count',0,None),
 'rating':('player_rating','rating_points',0,10),
 'red_card':('red_cards','count',0,None),
 'saves':('saves','count',0,None),
 'total_att_assist':('chances_created','count',0,None),
 'total_high_claim':('high_claims','count',0,None),
 'total_keeper_sweeper':('sweeper_actions','count',0,None),
 'total_scoring_att':('shots_total','count',0,None),
 'total_tackle':('tackles','count',0,None),
 'won_contest':('dribbles_completed','count',0,None),
 'yellow_card':('yellow_cards','count',0,None),
}

# Exact season-board titles observed in source metadata. Absence of "per match"
# is NOT enough to infer a total: unknown/new titles remain unverified.
TOTAL_TITLES = {
 'set piece goals conceded','set piece goals','xg difference','big chances missed',
 'big chances','clean sheets','corners','xg conceded','expected goals',
 'penalties conceded','penalties awarded','red cards','yellow cards',
 'touches in opposition box',
}


def season_observation(scope,key,value,extra=None,*,issues=None):
    if scope not in ('team','player'):raise ValueError('unsupported_entity_scope')
    definition=(TEAM if scope=='team' else PLAYER).get(key)
    if definition is None:return None
    code,unit,low,high=definition
    try:meta=json.loads(extra or '{}') if isinstance(extra,(str,type(None))) else extra
    except (ValueError,TypeError):meta={}
    if not isinstance(meta,dict):meta={}
    title=meta.get('stat_title');title=title.strip().lower() if isinstance(title,str) else ''
    # A key explicitly carrying per_90 is evidence of the source's denominator;
    # other player board keys carry no such evidence and remain unresolved.
    key_basis='per_90_minutes' if key.endswith('_per_90') else None
    title_basis=None
    if ' per 90' in title:title_basis='per_90_minutes'
    elif ' per match' in title:title_basis='per_match'
    elif title=='average possession':title_basis='mean'
    elif title in TOTAL_TITLES:title_basis='season_total'
    conflict=key_basis is not None and title_basis is not None and key_basis!=title_basis
    aggregation='unverified' if conflict else title_basis or key_basis or 'unverified'
    if conflict and issues is not None:issues.append('conflicting_aggregation_evidence')
    if code=='distance_covered' and meta.get('stat_format')=='meter':unit='metres'
    if aggregation=='unverified' and unit not in ('percent','rating_points','unverified_currency'):
        unit='unverified' # no total/rate ambiguity in a purported count value
    numeric=number(value,'count' if aggregation=='season_total' and unit=='count' else 'number',low,high)
    return {'metric_code':code,'value':numeric,'unit':unit,'aggregation':aggregation,
            'definition_version':('miaodi-season-board-metrics.v2' if conflict else 'miaodi-season-board-metrics.v1')}
