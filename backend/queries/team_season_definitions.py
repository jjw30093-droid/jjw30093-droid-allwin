"""Website-owned, finite aggregate field contract; no raw source dictionary."""
AGGREGATES = {
 'matches_played': ('count','season_total'),
 'avg_total_shots': ('count','per_match'), 'avg_shots_on_target': ('count','per_match'),
 'avg_possession': ('percent','mean'), 'avg_expected_goals': ('expected_count','per_match'),
 'avg_expected_goals_on_target': ('expected_count','per_match'),
 'avg_expected_goals_open_play': ('expected_count','per_match'),
 'avg_expected_goals_set_play': ('expected_count','per_match'),
 'avg_expected_goals_non_penalty': ('expected_count','per_match'),
 'avg_expected_goals_conceded': ('expected_count','per_match'),
 'avg_corners': ('count','per_match'), 'avg_fouls': ('count','per_match'),
 'avg_yellow_cards': ('count','per_match'), 'avg_red_cards': ('count','per_match'),
 'clean_sheets': ('count','season_total'), 'btts_matches': ('count','season_total'),
 'btts_pct': ('percent','ratio_of_totals'),
}
RATIO_UNITS = {
 'opp_half_pass_share':'percent','set_piece_xg_share':'percent','set_piece_xga_share':'percent',
 'opp_xg_per_shot':'expected_count_per_shot','shot_accuracy':'percent','box_shot_share':'percent',
 'big_chance_conversion':'percent','aerial_win_share':'percent','fast_break_xg_share':'percent',
 'box_touch_share':'percent','npxg_per_box_touch':'expected_count_per_touch',
 'def_action_density':'actions_per_100_opponent_passes','opp_territory_share':'percent',
 'corner_shot_rate':'shots_per_100_corners','finishing_delta':'goals_per_match',
 'gk_saves_above_expected':'goals_per_match',
}
STANDINGS_FIELDS = {
 'position':'position','matches_played':'played','wins':'wins','draws':'draws','losses':'losses',
 'goals_for':'goals_for','goals_against':'goals_against','goal_difference':'goal_diff','points':'points',
 'expected_goals_for':'xg','expected_goals_against':'xg_conceded','expected_points':'x_points',
 'expected_position':'x_position','points_above_expected':'x_points_diff','position_difference':'x_position_diff',
}
