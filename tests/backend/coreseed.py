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
    conn.commit()
    conn.close()
