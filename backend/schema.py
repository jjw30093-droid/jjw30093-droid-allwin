"""
schema.py — 表结构的唯一定义来源(init_db.py 建表、ingest_match.py 落库都从这里取列名)。
列名严格对齐 fotmob_client.py 里 parse_* 方法返回字典的 key。
"""

# ── fact_player_match_stats: parse_player_stats_records() 返回的固定宽表列 ──
# (Match_ID, Player_ID) 是自然主键,但落库策略不依赖唯一约束(见 ingest_match.py),
# 这里只按 parse 返回顺序声明列名 + 类型亲和。
PLAYER_STATS_COLUMNS = [
    ("Match_ID", "INTEGER"),
    ("Player_ID", "TEXT"),
    ("Player_Opta_ID", "TEXT"),
    ("Team_ID", "INTEGER"),
    ("is_goalkeeper", "INTEGER"),
    ("is_captain", "INTEGER"),
    ("shirt_number", "TEXT"),
    ("position_id", "INTEGER"),
    ("usual_position", "TEXT"),
    ("rating_title", "REAL"),
    ("minutes_played", "INTEGER"),
    ("player_name", "TEXT"),
    ("goals", "REAL"),
    ("assists", "REAL"),
    ("expected_goals", "REAL"),
    ("expected_assists", "REAL"),
    ("xg_and_xa", "REAL"),
    ("expected_goals_on_target_variant", "REAL"),
    ("expected_goals_non_penalty", "REAL"),
    ("ShotsOnTarget", "REAL"),
    ("ShotsOffTarget", "REAL"),
    ("shot_accuracy", "REAL"),
    ("blocked_shots", "REAL"),
    ("big_chance_missed_title", "REAL"),
    ("shots_woodwork", "REAL"),
    ("missed_penalty", "REAL"),
    ("shotmap", "TEXT"),
    ("accurate_passes", "REAL"),
    ("chances_created", "REAL"),
    ("big_chance_created_team_title", "REAL"),
    ("passes_into_final_third", "REAL"),
    ("accurate_crosses", "REAL"),
    ("long_balls_accurate", "REAL"),
    ("touches", "REAL"),
    ("touches_opp_box", "REAL"),
    ("dispossessed", "REAL"),
    ("dribbles_succeeded", "REAL"),
    ("matchstats.headers.tackles", "REAL"),
    ("shot_blocks", "REAL"),
    ("clearances", "REAL"),
    ("headed_clearance", "REAL"),
    ("interceptions", "REAL"),
    ("recoveries", "REAL"),
    ("dribbled_past", "REAL"),
    ("ground_duels_won", "REAL"),
    ("aerials_won", "REAL"),
    ("defensive_actions", "REAL"),
    ("last_man_tackle", "REAL"),
    ("clearance_off_the_line", "REAL"),
    ("duel_won", "REAL"),
    ("duel_lost", "REAL"),
    ("fouls", "REAL"),
    ("was_fouled", "REAL"),
    ("penalties_won", "REAL"),
    ("conceded_penalties", "REAL"),
    ("errors_led_to_goal", "REAL"),
    ("corners", "REAL"),
    ("Offsides", "REAL"),
    ("owngoal", "REAL"),
    ("fantasy_points", "REAL"),
    ("saves", "REAL"),
    ("goals_conceded", "REAL"),
    ("expected_goals_on_target_faced", "REAL"),
    ("goals_prevented", "REAL"),
    ("keeper_diving_save", "REAL"),
    ("saves_inside_box", "REAL"),
    ("keeper_sweeper", "REAL"),
    ("punches", "REAL"),
    ("player_throws", "REAL"),
    ("keeper_high_claim", "REAL"),
    ("saved_penalties", "REAL"),
    ("saved_penalties_in_shootout", "REAL"),
    ("physical_metrics_topspeed", "REAL"),
    ("physical_metrics_distance_covered", "REAL"),
    ("physical_metrics_walking", "REAL"),
    ("physical_metrics_running", "REAL"),
    ("physical_metrics_sprinting", "REAL"),
    ("physical_metrics_number_of_sprints", "REAL"),
]

# ── fact_team_match_stats: parse_team_stats_records() 的 key 因比赛/联赛而异 ──
# 方案:核心列(稳定存在) + extra_json 兜底(动态 key 原样存 JSON)。
# 原因:
#   - EAV(长表)虽然完全灵活,但每次查询都要 pivot,简单的"某场比赛控球率"都要写子查询。
#   - 纯固定宽表需要提前知道所有可能出现的统计项,而 FotMob 不同联赛/赛事可用的统计
#     类别不同,新增字段会频繁触发 ALTER TABLE,且历史缺失字段会有大量 NULL 列。
#   - 核心列(Match_ID/Team_ID/Period/Goals)覆盖率 100%、査询/JOIN 高频,值得做成真列;
#     其余动态字段整体存进 extra_json(TEXT,JSON 编码),不丢数据,需要时用 SQLite 的
#     json_extract(extra_json, '$.key') 取值,以后发现某个字段稳定出现且高频查询,
#     再单独提升为真列。
#
# 已确认的数据源限制(2020/21~2025/26 全量核对过,非解析 bug):
#   老赛季 content.stats.Periods 里只有 "All"，没有 "FirstHalf"/"SecondHalf"——
#   FotMob 从大约 2022/23 赛季中段才开始提供半场拆分统计。受影响场次(每队仅 1 行
#   即 Period='All'，而非完整的 3 个 Period × 2 队 = 6 行):
#   2020/2021 373/380、2021/2022 377/380、2022/2023 214/380；2023/2024 及以后
#   全部有完整半场拆分。查询半场数据前先确认赛季/场次是否落在此范围内。
TEAM_STATS_CORE_COLUMNS = [
    ("Match_ID", "INTEGER"),
    ("Team_ID", "INTEGER"),
    ("Period", "TEXT"),
    ("Goals", "REAL"),
]

DIM_MATCH_COLUMNS = [
    ("Match_ID", "INTEGER PRIMARY KEY"),
    ("Season", "TEXT"),
    ("League_ID", "INTEGER"),
    ("Date", "TEXT"),
    ("Home_Team_ID", "INTEGER"),
    ("Away_Team_ID", "INTEGER"),
    ("Home_Team_Name", "TEXT"),
    ("Away_Team_Name", "TEXT"),
    ("home_score", "INTEGER"),
    ("away_score", "INTEGER"),
    ("status", "TEXT"),
    ("Referee", "TEXT"),
    ("Match_Round", "TEXT"),
    ("Temperature", "TEXT"),
    ("Wind_Speed", "TEXT"),
    ("Who_Lost_On_Penalties", "TEXT"),
]

DIM_PLAYER_COLUMNS = [
    ("Player_ID", "TEXT PRIMARY KEY"),
    ("Player_Name", "TEXT"),
]

SHOTMAP_COLUMNS = [
    ("Match_ID", "INTEGER"),
    ("Player_ID", "TEXT"),
    ("Team_ID", "INTEGER"),
    ("Minute", "INTEGER"),
    ("Period", "TEXT"),
    ("X_Coord", "REAL"),
    ("Y_Coord", "REAL"),
    ("xG", "REAL"),
    ("xGOT", "REAL"),
    ("Situation", "TEXT"),
    ("Outcome", "TEXT"),
    ("Shot_Type", "TEXT"),
]


def _quote(col: str) -> str:
    """带点号的列名(如 matchstats.headers.tackles)需要用双引号包裹。"""
    return f'"{col}"'


# ── fact_league_table: parse_league_table() 返回，联赛积分榜(all/home/away/form/xg 五档) ──
# 自然键 (League_ID, Season, table_type, Team_ID)，不强制唯一约束，落库前按
# (League_ID, Season) 先删后插。核心列覆盖 all/home/away/form 四档共有字段；
# xg 档独有的 xg/xg_conceded/x_points/x_position 在其它档为 NULL；
# 其余字段(goalsScored 冗余、xgDiff 等派生量、pageUrl 等)进 extra_json。
LEAGUE_TABLE_CORE_COLUMNS = [
    ("League_ID", "INTEGER"),
    ("Season", "TEXT"),
    ("table_type", "TEXT"),
    ("Team_ID", "INTEGER"),
    ("Team_Name", "TEXT"),
    ("position", "INTEGER"),
    ("played", "INTEGER"),
    ("wins", "INTEGER"),
    ("draws", "INTEGER"),
    ("losses", "INTEGER"),
    ("goals_for", "INTEGER"),
    ("goals_against", "INTEGER"),
    ("goal_diff", "INTEGER"),
    ("points", "INTEGER"),
    ("deduction", "INTEGER"),
    ("qual_color", "TEXT"),
    ("xg", "REAL"),
    ("xg_conceded", "REAL"),
    ("x_points", "REAL"),
    ("x_position", "INTEGER"),
]

# ── fact_match_events: parse_match_events() 返回，比赛事件时间线(按数组顺序一行一事件) ──
# 无稳定行 id，按 Match_ID 先删后插。event_type ∈ {Goal, Card, Substitution, AddedTime, Half}，
# 不同类型只填各自相关字段，其余为 NULL；shotmapEvent 等嵌套细节进 extra_json。
MATCH_EVENTS_CORE_COLUMNS = [
    ("Match_ID", "INTEGER"),
    ("event_index", "INTEGER"),
    ("event_type", "TEXT"),
    ("minute", "INTEGER"),
    ("overload_time", "INTEGER"),
    ("is_home", "INTEGER"),
    ("home_score", "INTEGER"),
    ("away_score", "INTEGER"),
    ("player_id", "TEXT"),
    ("player_name", "TEXT"),
    ("card_type", "TEXT"),
    ("assist_player_id", "TEXT"),
    ("assist_player_name", "TEXT"),
    ("sub_in_player_id", "TEXT"),
    ("sub_in_player_name", "TEXT"),
    ("sub_out_player_id", "TEXT"),
    ("sub_out_player_name", "TEXT"),
    ("minutes_added", "INTEGER"),
    ("event_id", "TEXT"),
]

# ── fact_match_lineup: parse_lineup_records() 返回，阵容与阵型(每场每队每人一行) ──
# 只含出场名单(首发 starters + 替补 subs)，不含教练/伤停名单。
# 自然键 (Match_ID, Player_ID)，按 Match_ID 先删后插。
MATCH_LINEUP_CORE_COLUMNS = [
    ("Match_ID", "INTEGER"),
    ("Team_ID", "INTEGER"),
    ("is_home", "INTEGER"),
    ("formation", "TEXT"),
    ("Player_ID", "TEXT"),
    ("player_name", "TEXT"),
    ("shirt_number", "TEXT"),
    ("position_id", "INTEGER"),
    ("usual_position_id", "INTEGER"),
    ("is_starter", "INTEGER"),
    ("is_captain", "INTEGER"),
    ("country_code", "TEXT"),
    ("market_value", "INTEGER"),
    ("rating", "REAL"),
    ("sub_in_time", "INTEGER"),
    ("sub_out_time", "INTEGER"),
]

# ── fact_season_player_stats: parse_season_player_stats() 返回，赛季球员榜单 ──
# 每个统计维度(stat_name，如 goals/assists/rating 等)一份全量排名，来自联赛 API
# stats.players[].fetchAllUrl 拉取的完整 JSON(不止 topThree)。
# 自然键 (League_ID, Season, stat_name, Player_ID)，按 (League_ID, Season) 先删后插。
SEASON_PLAYER_STATS_CORE_COLUMNS = [
    ("League_ID", "INTEGER"),
    ("Season", "TEXT"),
    ("stat_name", "TEXT"),
    ("Player_ID", "TEXT"),
    ("Player_Name", "TEXT"),
    ("Team_ID", "INTEGER"),
    ("Team_Name", "TEXT"),
    ("rank", "INTEGER"),
    ("value", "REAL"),
]
