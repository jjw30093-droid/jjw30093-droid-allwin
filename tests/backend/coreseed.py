"""测试用 core(allwin.db)最小造数工具。"""

from backend.db.connections import connect_rw


def seed_core_schema(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS dim_match (
            Match_ID INTEGER PRIMARY KEY, Season TEXT, League_ID INTEGER, Date TEXT,
            Home_Team_ID INTEGER, Away_Team_ID INTEGER, Home_Team_Name TEXT, Away_Team_Name TEXT,
            home_score INTEGER, away_score INTEGER, status TEXT, Match_Round TEXT,
            kickoff_at_utc TEXT)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS dim_team_i18n (
            Team_ID INTEGER PRIMARY KEY, name_en TEXT, name_zh TEXT, source TEXT, updated_at TEXT)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS fact_league_table (
            League_ID INTEGER, Season TEXT, table_type TEXT, Team_ID INTEGER, Team_Name TEXT,
            position INTEGER, played INTEGER, wins INTEGER, draws INTEGER, losses INTEGER,
            goals_for INTEGER, goals_against INTEGER, goal_diff INTEGER, points INTEGER,
            deduction INTEGER, qual_color TEXT, xg REAL, xg_conceded REAL, x_points REAL, x_position INTEGER)"""
    )
    # 混合表:免费字段(射门/射正/控球/xG/xGOT)与付费字段(角球/牌/零封/BTTS)
    # 同表——布景两类都造,测试才能证明 API 只投影免费字段
    conn.execute(
        """CREATE TABLE IF NOT EXISTS silver_team_season_stats (
            League_ID INTEGER, Season TEXT, Team_ID INTEGER, matches_played INTEGER,
            avg_total_shots REAL, avg_shots_on_target REAL, avg_possession REAL,
            avg_corners REAL, avg_fouls REAL, avg_yellow_cards REAL,
            avg_expected_goals REAL, avg_expected_goals_non_penalty REAL,
            avg_expected_goals_open_play REAL, avg_expected_goals_set_play REAL,
            avg_expected_goals_on_target REAL, avg_touches_opp_box REAL,
            clean_sheets INTEGER, btts_matches INTEGER, btts_pct REAL,
            updated_at TEXT, avg_red_cards REAL)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS fact_season_player_stats (
            League_ID INTEGER, Season TEXT, stat_name TEXT, Player_ID TEXT, Player_Name TEXT,
            Team_ID INTEGER, Team_Name TEXT, rank INTEGER, value REAL, extra_json TEXT)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS dim_player_i18n (
            Player_ID TEXT, name_en TEXT, name_zh TEXT, name_zh_short TEXT,
            source TEXT, model TEXT, confidence REAL, needs_review INTEGER, updated_at TEXT)"""
    )


def insert_match(conn, match_id, league_id=47, season="2026/2027", date="2027-04-01",
                 home_id=1001, away_id=1002, home="Arsenal", away="Chelsea",
                 status="NotStarted", home_score=None, away_score=None, rnd="1",
                 kickoff_at_utc=None):
    conn.execute(
        "INSERT OR REPLACE INTO dim_match (Match_ID, Season, League_ID, Date, Home_Team_ID, Away_Team_ID,"
        " Home_Team_Name, Away_Team_Name, home_score, away_score, status, Match_Round, kickoff_at_utc)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (match_id, season, league_id, date, home_id, away_id, home, away, home_score, away_score, status, rnd,
         kickoff_at_utc),
    )


def seed_basic_core(data_dir):
    """标准布景:英超 2 场(1 未来 1 完赛)+ 西甲 1 场 + 中文名 + 简单积分榜。"""
    conn = connect_rw("core")
    seed_core_schema(conn)
    insert_match(conn, 9001, date="2027-04-01", status="NotStarted")
    insert_match(conn, 9002, date="2026-05-01", status="Finish", home_score=2, away_score=0, rnd="38")
    insert_match(conn, 9101, league_id=87, season="2025/2026", date="2026-05-10",
                 home_id=2001, away_id=2002, home="Barcelona", away="Real Madrid",
                 status="Finish", home_score=1, away_score=1)
    conn.execute("INSERT OR REPLACE INTO dim_team_i18n VALUES (1001,'Arsenal','阿森纳','t','')")
    conn.execute("INSERT OR REPLACE INTO dim_team_i18n VALUES (1002,'Chelsea','切尔西','t','')")
    conn.execute(
        "INSERT INTO fact_league_table (League_ID, Season, table_type, Team_ID, Team_Name, position,"
        " played, wins, draws, losses, goals_for, goals_against, goal_diff, points, qual_color)"
        " VALUES (47,'2025/2026','all',1001,'Arsenal',1,38,28,6,4,88,29,59,90,'#2AD572')"
    )
    # 球队赛季统计:47 与 87 各一行;付费字段(角球/牌/零封/BTTS)造出显眼的
    # 哨兵值,免费投影测试全 JSON 扫描它们绝不能出现
    for lid, season, tid in ((47, "2025/2026", 1001), (87, "2025/2026", 2001)):
        conn.execute(
            "INSERT INTO silver_team_season_stats (League_ID, Season, Team_ID, matches_played,"
            " avg_total_shots, avg_shots_on_target, avg_possession, avg_expected_goals,"
            " avg_expected_goals_on_target, avg_corners, avg_yellow_cards, avg_red_cards,"
            " clean_sheets, btts_matches, btts_pct)"
            " VALUES (?, ?, ?, 38, 15.2, 6.1, 58.3, 2.11, 1.87, 777.7, 888.8, 999.9, 21, 17, 44.7)",
            (lid, season, tid),
        )
    # 球员榜:47 一条带中文映射,87 一条无映射(回退英文名)
    conn.execute(
        "INSERT INTO fact_season_player_stats (League_ID, Season, stat_name, Player_ID,"
        " Player_Name, Team_ID, Team_Name, rank, value)"
        " VALUES (47,'2025/2026','goals','p100','Test Striker',1001,'Arsenal',1,27.0)"
    )
    conn.execute(
        "INSERT INTO fact_season_player_stats (League_ID, Season, stat_name, Player_ID,"
        " Player_Name, Team_ID, Team_Name, rank, value)"
        " VALUES (87,'2025/2026','goals','p200','Laliga Striker',2001,'Barcelona',1,30.0)"
    )
    conn.execute(
        "INSERT INTO dim_player_i18n (Player_ID, name_en, name_zh, name_zh_short)"
        " VALUES ('p100','Test Striker','测试前锋全名','测试前锋')"
    )
    conn.commit()
    conn.close()
