"""Explicit field mappings. Name equality never establishes metric equivalence.

Counts are actual observations only: absent source rows stay absent/null despite
legacy source-specific missing_is_zero defaults. Each feed keeps its own series.
"""
from .contract import number

OPTA_COUNTS={
 'goals':'goals','ownGoals':'own_goals','totalScoringAtt':'shots_total',
 'ontargetScoringAtt':'shots_on_target','shotOffTarget':'shots_off_target',
 'blockedScoringAtt':'shots_blocked','hitWoodwork':'shots_hit_woodwork',
 'bigChanceCreated':'big_chances_created','bigChanceMissed':'big_chances_missed',
 'goalAssist':'goal_assists','totalPass':'passes_attempted','accuratePass':'passes_completed',
 'totalCross':'crosses_attempted','accurateCross':'crosses_completed',
 'totalThroughBall':'through_balls_attempted','accurateThroughBall':'through_balls_completed',
 'touches':'touches','unsuccessfulTouch':'unsuccessful_touches',
 'totalContest':'dribbles_attempted','wonContest':'dribbles_completed',
 'totalTackle':'tackles_attempted','wonTackle':'tackles_won','interception':'interceptions',
 'blockedPass':'passes_blocked','totalClearance':'clearances','ballRecovery':'ball_recoveries',
 'aerialWon':'aerial_duels_won','aerialLost':'aerial_duels_lost','duelWon':'duels_won',
 'duelLost':'duels_lost','errorLeadToShot':'errors_leading_to_shot','errorLeadToGoal':'errors_leading_to_goal',
 'saves':'saves','totalOffside':'offsides','cornerTaken':'corners_taken',
 'penaltyWon':'penalties_won','penaltyConceded':'penalties_conceded',
 'carries':'carries','progressiveCarries':'progressive_carries',
}
VIP_COUNTS={
 'Goals':'goals','goals':'goals','total_shots':'shots_total','ShotsOnTarget':'shots_on_target',
 'ShotsOffTarget':'shots_off_target','blocked_shots':'shots_blocked','shots_woodwork':'shots_hit_woodwork',
 'assists':'goal_assists','accurate_passes':'passes_completed','accurate_passes_total':'passes_attempted',
 'accurate_crosses':'crosses_completed','accurate_crosses_total':'crosses_attempted',
 'touches':'touches','touches_opp_box':'touches_in_opposition_box','interceptions':'interceptions',
 'recoveries':'ball_recoveries','clearances':'clearances','saves':'saves','corners':'corners_taken',
 'Offsides':'offsides','owngoal':'own_goals','penalties_won':'penalties_won',
 'conceded_penalties':'penalties_conceded','duel_won':'duels_won','duel_lost':'duels_lost',
 'errors_led_to_goal':'errors_leading_to_goal','big_chance_missed_title':'big_chances_missed',
 'big_chance_created_team_title':'big_chances_created','minutes_played':'minutes_played',
}
DEFINITIONS={}
for provider,items in [('fotmob',VIP_COUNTS),('opta',OPTA_COUNTS)]:
 for source,code in items.items():DEFINITIONS[provider,source]=(code,'minutes' if code=='minutes_played' else 'count','count',0,None)
for provider,source,code in [('fotmob','BallPossesion','possession_percent'),('opta','possessionPercentage','possession_percent')]:
 DEFINITIONS[provider,source]=(code,'percent','number',0,100)
for source,code in [('expected_goals','expected_goals'),('expected_assists','expected_assists'),('expected_goals_on_target_variant','expected_goals_on_target'),('expected_goals_non_penalty','expected_goals_non_penalty')]:
 DEFINITIONS['fotmob',source]=(code,'expected_count','number',0,None)


def definition_for(provider,key,entity_scope=None):
    if entity_scope not in (None,'team','player'):
        raise ValueError('invalid_metric_entity_scope')
    if (provider,key) in SCOPED_KEYS:
        return SCOPED_DEFINITIONS.get((provider,entity_scope,key))
    return DEFINITIONS.get((provider,key))


def definition_status(provider,key,entity_scope=None):
    if definition_for(provider,key,entity_scope) is None:return 'unmapped'
    return 'unverified' if (provider,entity_scope,key) in UNVERIFIED_DEFINITIONS else 'mapped'


def definition_version(provider,key,entity_scope=None):
    return SCOPED_DEFINITION_VERSIONS.get((provider,entity_scope,key),
        'miaodi-observed-metrics.v2' if (provider,key) in SCOPED_KEYS else 'miaodi-observed-metrics.v1')


def observation(provider,key,value,*,entity_scope=None):
    definition=definition_for(provider,key,entity_scope)
    if definition is None:return None
    code,unit,kind,low,high=definition
    return {'metric_code':code,'value':number(value,kind,low,high),'unit':unit,
            'definition_version':definition_version(provider,key,entity_scope)}

# Source UI section markers are not observations. Keep this classification
# explicit rather than filling their NULL values with numerical zero.
NON_METRIC_FIELDS={'fotmob':{'shots','physical_metrics','passes_section','defense','duels','discipline'},'opta':set()}
ADDITIONAL_VIP_COUNTS={
 'big_chance':'big_chances','yellow_cards':'yellow_cards','red_cards':'red_cards',
 'shots_inside_box':'shots_inside_penalty_area','shots_outside_box':'shots_outside_penalty_area',
 'passes':'passes_attempted','own_half_passes':'passes_completed_own_half',
 'opposition_half_passes':'passes_completed_opposition_half',
 'long_balls_accurate':'long_passes_completed','long_balls_accurate_total':'long_passes_attempted',
 'matchstats.headers.tackles':'tackles',
 'shot_blocks':'shots_blocked_defensively','keeper_saves':'saves',
 'ground_duels_won':'ground_duels_won','ground_duels_won_total':'ground_duels_contested',
 'aerials_won':'aerial_duels_won','aerials_won_total':'aerial_duels_contested',
 'dribbles_succeeded':'dribbles_completed','dribbles_succeeded_total':'dribbles_attempted',
 'fouls':'fouls_committed','was_fouled':'fouls_won','missed_penalty':'penalties_missed',
 'chances_created':'chances_created','passes_into_final_third':'passes_into_final_third',
 'dispossessed':'times_dispossessed','headed_clearance':'headed_clearances',
 'dribbled_past':'times_dribbled_past','defensive_actions':'defensive_actions',
 'last_man_tackle':'last_defender_tackles','clearance_off_the_line':'goal_line_clearances',
 'goals_conceded':'goals_conceded','keeper_diving_save':'diving_saves',
 'saves_inside_box':'saves_inside_penalty_area','keeper_sweeper':'sweeper_actions',
 'punches':'keeper_punches','keeper_high_claim':'high_claims',
 'saved_penalties':'penalties_saved','saved_penalties_in_shootout':'shootout_penalties_saved',
 'line_breaking_passes':'line_breaking_passes','physical_metrics_number_of_sprints':'sprints',
}
for source,code in ADDITIONAL_VIP_COUNTS.items():DEFINITIONS['fotmob',source]=(code,'count','count',0,None)
for source,code in [('expected_goals_open_play','expected_goals_open_play'),('expected_goals_set_play','expected_goals_set_play'),('expected_goals_on_target','expected_goals_on_target'),('expected_goals_on_target_faced','expected_goals_on_target_faced'),('xg_and_xa','expected_goals_plus_assists')]:
 DEFINITIONS['fotmob',source]=(code,'expected_count','number',0,None)
DEFINITIONS['fotmob','goals_prevented']=('goals_prevented','expected_count','number',None,None)
DEFINITIONS['fotmob','rating_title']=('player_rating','rating_points','number',0,10)
DEFINITIONS['fotmob','fantasy_points']=('fantasy_points','points','number',None,None)
for source,code in [('shot_accuracy','shot_accuracy_percent'),('attacking_zone_left','attack_share_left_percent'),('attacking_zone_center','attack_share_centre_percent'),('attacking_zone_right','attack_share_right_percent')]:
 DEFINITIONS['fotmob',source]=(code,'percent','number',0,100)
# Values preserved without claiming an unverified physical measurement unit.
UNVERIFIED_UNITS={}
for source,code in [('physical_metrics_topspeed','top_speed'),('physical_metrics_distance_covered','distance_covered'),('physical_metrics_walking','walking_distance'),('physical_metrics_jogging','jogging_distance'),('physical_metrics_running','running_distance'),('physical_metrics_sprinting','sprinting_distance')]:
 DEFINITIONS['fotmob',source]=(code,'unverified','number',0,None)
 UNVERIFIED_UNITS['fotmob',source]=True

# The source reuses this name for team and player observations. In real match
# samples, team totals and goalkeeper values are different measures; existing
# website code labels the latter as hand distributions. Do not assert that a
# player's count means touchline throw-ins or infer its subtype from their role.
# Preserve it under a neutral own code and expose the definition evidence gap.
SCOPED_DEFINITIONS={
 ('fotmob','team','player_throws'):('throw_ins','count','count',0,None),
 ('fotmob','player','player_throws'):('throws','count','count',0,None),
 ('opta','team','ppda'):('passes_per_defensive_action','ratio','number',0,None),
}
SCOPED_KEYS={(provider,key) for provider,scope,key in SCOPED_DEFINITIONS}
UNVERIFIED_DEFINITIONS={('fotmob','player','player_throws')}
SCOPED_DEFINITION_VERSIONS={('opta','team','ppda'):'miaodi-observed-metrics.v3'}
