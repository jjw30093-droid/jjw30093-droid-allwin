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
