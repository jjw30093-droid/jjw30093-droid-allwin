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
            deduction INTEGER, qual_color TEXT, xg REAL, xg_conceded REAL, x_points REAL,
            x_position INTEGER, extra_json TEXT)"""
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
    # ── 单场事实五表(/matches/{id}/report;列名镜像 backend/schema.py 与真实库) ──
    conn.execute(
        """CREATE TABLE IF NOT EXISTS fact_match_lineup (
            Match_ID INTEGER, Team_ID INTEGER, is_home INTEGER, formation TEXT,
            Player_ID TEXT, player_name TEXT, shirt_number TEXT, position_id INTEGER,
            usual_position_id INTEGER, is_starter INTEGER, is_captain INTEGER,
            country_code TEXT, market_value INTEGER, rating REAL,
            sub_in_time INTEGER, sub_out_time INTEGER, extra_json TEXT)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS fact_match_events (
            Match_ID INTEGER, event_index INTEGER, event_type TEXT, minute INTEGER,
            overload_time INTEGER, is_home INTEGER, home_score INTEGER, away_score INTEGER,
            player_id TEXT, player_name TEXT, card_type TEXT,
            assist_player_id TEXT, assist_player_name TEXT,
            sub_in_player_id TEXT, sub_in_player_name TEXT,
            sub_out_player_id TEXT, sub_out_player_name TEXT,
            minutes_added INTEGER, event_id TEXT, extra_json TEXT)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS fact_shotmap (
            Match_ID INTEGER, Player_ID TEXT, Team_ID INTEGER, Minute INTEGER,
            Period TEXT, X_Coord REAL, Y_Coord REAL, xG REAL, xGOT REAL,
            Situation TEXT, Outcome TEXT, Shot_Type TEXT)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS fact_team_match_stats (
            Match_ID INTEGER, Team_ID INTEGER, Period TEXT, Goals REAL, extra_json TEXT)"""
    )
    # 真实库该表 76 列;测试只镜像 report 查询 SELECT 的列 + 带点列名(那正是
    # 手写测试 schema 最容易漏掉/写错的地方,必须与真实库一字不差)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS fact_player_match_stats (
            Match_ID INTEGER, Player_ID TEXT, Team_ID INTEGER, is_goalkeeper INTEGER,
            rating_title REAL, minutes_played INTEGER, player_name TEXT,
            goals REAL, assists REAL, expected_goals REAL, expected_assists REAL,
            ShotsOnTarget REAL, ShotsOffTarget REAL, accurate_passes REAL,
            chances_created REAL, touches REAL, touches_opp_box REAL,
            dribbles_succeeded REAL, "matchstats.headers.tackles" REAL,
            clearances REAL, interceptions REAL, duel_won REAL, aerials_won REAL,
            fouls REAL, corners REAL, Offsides REAL, saves REAL,
            goals_conceded REAL, goals_prevented REAL,
            expected_goals_on_target_faced REAL)"""
    )


# 哨兵:season 未显式给出时按 (league_id, date) 从制度表推导(2026-08-25,
# CLAUDE.md §6.3)——测试布景与生产走同一份推导逻辑,不再各自手填一个可能与
# 日期矛盾的赛季字符串(那正是 dim_match 触发器要 ABORT 的事故形态)。
# 显式传入的 season 保持原样(触发器会校验它与日期一致)。
_AUTO_SEASON = object()


def insert_match(conn, match_id, league_id=47, season=_AUTO_SEASON, date="2027-04-01",
                 home_id=1001, away_id=1002, home="Arsenal", away="Chelsea",
                 status="NotStarted", home_score=None, away_score=None, rnd="1",
                 kickoff_at_utc=None):
    if season is _AUTO_SEASON:
        from backend.season_regime import season_for_match

        season = season_for_match(conn, league_id, date)
    conn.execute(
        "INSERT OR REPLACE INTO dim_match (Match_ID, Season, League_ID, Date, Home_Team_ID, Away_Team_ID,"
        " Home_Team_Name, Away_Team_Name, home_score, away_score, status, Match_Round, kickoff_at_utc)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (match_id, season, league_id, date, home_id, away_id, home, away, home_score, away_score, status, rnd,
         kickoff_at_utc),
    )


def seed_match_report(conn, match_id=9002, home_id=1001, away_id=1002):
    """给一场完赛比赛种五张事实表(/matches/{id}/report 测试用)。

    刻意内置三类哨兵,供响应全文扫描断言"绝不出现":
    - Period='FirstHalf' 球队统计行,possession=111.1(证明只取 Period='All');
    - Team_ID=99999 的射门行,xG=0.9999(证明主客队之外的脏数据被过滤);
    - player_stats.rating_title=9.99(证明评分取自 lineup.rating=7.7,不取本表)。
    """
    # 2026-08-25 起查询层读 verticalLayout(纵向球场):x=横向(0.5=中路),
    # y=纵深(0.1=本方球门端),width=本行格宽。数值取自真实来源关系
    # v.x=1-h.y、v.y=h.x(此前 seed 写的是 horizontalLayout 等价坐标)。
    layout = lambda x, y, w: (  # noqa: E731
        f'{{"verticalLayout": {{"x": {x}, "y": {y}, "width": {w}}}}}'
    )
    lineup_rows = [
        # (team, is_home, formation, pid, name, shirt, upos, starter, captain, rating, layout)
        (home_id, 1, "4-3-3", "p100", "Test Striker", "9", 3, 1, 0, 7.7, layout(0.5, 0.87, 1)),
        (home_id, 1, "4-3-3", "p101", "Home Keeper", "1", 0, 1, 1, 7.0, layout(0.5, 0.1, 1)),
        (home_id, 1, "4-3-3", "p102", "Home Bench", "20", 2, 0, 0, 6.4, None),
        (away_id, 0, "4-4-2", "p200", "Away Defender", "4", 1, 1, 0, 6.6, layout(0.5, 0.292, 0.25)),
        (away_id, 0, "4-4-2", "p201", "Away Bench", "17", 3, 0, 0, None, None),
    ]
    for t, ih, form, pid, name, shirt, upos, st, cap, rating, extra in lineup_rows:
        conn.execute(
            "INSERT INTO fact_match_lineup (Match_ID, Team_ID, is_home, formation, Player_ID,"
            " player_name, shirt_number, usual_position_id, is_starter, is_captain, rating,"
            " sub_in_time, sub_out_time, extra_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)",
            (match_id, t, ih, form, pid, name, shirt, upos, st, cap, rating, extra),
        )
    # extra_json 最后一个字段:HT/FT 用 halfStrShort 区分中场/全场(见
    # backend/queries/match_report.py::HALF_KINDS);乌龙球用顶层 ownGoal——
    # event_index=7 的判据必须只认顶层 ownGoal,不看 shotmapEvent.isOwnGoal
    # (event_index=8 是故意矛盾的哨兵:ownGoal=null 但 shotmapEvent.isOwnGoal
    # =true,真实库里有 2 条这种矛盾,查询层必须判为非乌龙球)。乌龙球事件
    # is_home 是**受益方**(全库 1066/1066 核验),不是乌龙球员所属队——这里
    # is_home=1(主队受益),球员却是客队的 p200,复刻真实语义。
    events = [
        (0, "Goal", 24, 0, 1, 1, 0, "p100", "Test Striker", None, "p101", "Home Keeper", None),
        (1, "Card", 40, 0, 0, None, None, "p200", "Away Defender", "Yellow", None, None, None),
        (2, "Half", 45, 0, None, None, None, None, None, None, None, None,
         '{"halfStrShort": "HT"}'),
        (3, "AddedTime", 90, 0, None, None, None, None, None, None, None, None, None),
        (4, "Substitution", 60, 0, 1, None, None, None, None, None, None, None, None),
        (5, "VAR", 70, 1, 0, None, None, "p200", "Away Defender", None, None, None, None),
        (6, "Half", 90, 0, None, 2, 1, None, None, None, None, None,
         '{"halfStrShort": "FT"}'),
        (7, "Goal", 75, 0, 1, 2, 0, "p200", "Away Defender", None, None, None,
         '{"ownGoal": true}'),
        (8, "Goal", 80, 0, 0, 1, 0, "p100", "Test Striker", None, None, None,
         '{"ownGoal": null, "shotmapEvent": {"isOwnGoal": true}}'),
    ]
    for idx, etype, minute, overload, ih, hs, as_, pid, pname, card, apid, apname, extra in events:
        conn.execute(
            "INSERT INTO fact_match_events (Match_ID, event_index, event_type, minute,"
            " overload_time, is_home, home_score, away_score, player_id, player_name,"
            " card_type, assist_player_id, assist_player_name, minutes_added, extra_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (match_id, idx, etype, minute, overload, ih, hs, as_, pid, pname, card,
             apid, apname, 4 if etype == "AddedTime" else None, extra),
        )
    shots = [
        # (pid, team, minute, period, x, y, xg, outcome)
        ("p100", home_id, 24, "FirstHalf", 95.0, 40.0, 0.31, "Goal"),
        ("p200", away_id, 55, "SecondHalf", 88.0, 30.0, 0.05, "Miss"),
        # 哨兵:主客队之外的脏 Team_ID,必须被过滤
        ("p999", 99999, 70, "SecondHalf", 90.0, 34.0, 0.9999, "Miss"),
        # 点球大战射门:后端保留在 DTO 里(前端负责排除出射门图)
        ("p100", home_id, None, "PenaltyShootout", 94.0, 34.0, 0.79, "Goal"),
    ]
    for pid, t, minute, period, x, y, xg, outcome in shots:
        conn.execute(
            "INSERT INTO fact_shotmap (Match_ID, Player_ID, Team_ID, Minute, Period,"
            " X_Coord, Y_Coord, xG, xGOT, Situation, Outcome, Shot_Type)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 'RegularPlay', ?, 'RightFoot')",
            (match_id, pid, t, minute, period, x, y, xg, outcome),
        )
    conn.execute(
        "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
        " VALUES (?, ?, 'All', 2, ?)",
        (match_id, home_id,
         '{"BallPossesion": 61, "expected_goals": 2.31, "total_shots": 14,'
         ' "ShotsOnTarget": 6, "matchstats.headers.tackles": 12,'
         ' "corners": 7, "yellow_cards": 1, "shots": null}'),
    )
    conn.execute(
        "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
        " VALUES (?, ?, 'All', 0, ?)",
        (match_id, away_id,
         '{"BallPossesion": 39, "expected_goals": 0.87, "total_shots": 5, "ShotsOnTarget": 1}'),
    )
    # 哨兵:半场行,数值绝不能进响应(查询必须只取 Period='All')
    conn.execute(
        "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
        " VALUES (?, ?, 'FirstHalf', 1, ?)",
        (match_id, home_id, '{"BallPossesion": 111.1, "total_shots": 222.2}'),
    )
    player_stats = [
        # (pid, team, gk, rating_title哨兵, minutes, name, goals)
        ("p100", home_id, 0, 9.99, 90, "Test Striker", 1),
        ("p200", away_id, 0, 9.99, 90, "Away Defender", 0),
    ]
    for pid, t, gk, rt, mins, name, goals in player_stats:
        conn.execute(
            'INSERT INTO fact_player_match_stats (Match_ID, Player_ID, Team_ID, is_goalkeeper,'
            ' rating_title, minutes_played, player_name, goals, "matchstats.headers.tackles")'
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 3)",
            (match_id, pid, t, gk, rt, mins, name, goals),
        )


def seed_basic_core(data_dir):
    """标准布景:英超 2 场(1 未来 1 完赛)+ 西甲 1 场 + 中文名 + 简单积分榜。"""
    conn = connect_rw("core")
    seed_core_schema(conn)
    insert_match(conn, 9001, date="2027-04-01", status="NotStarted")
    # 9002:已完赛比赛,与 9001 同处 2026/2027 赛季(2026-08-25 起赛季由日期
    # 推导——旧布景 date=2026-05-01 配 Season='2026/2027' 是自相矛盾的错标,
    # 正是触发器要拒绝的形态;改成赛季初的真实日期,语义不变:一未来一完赛)
    insert_match(conn, 9002, date="2026-08-22", status="Finish", home_score=2, away_score=0, rnd="1")
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
            " avg_expected_goals_on_target, avg_expected_goals_open_play,"
            " avg_expected_goals_set_play, avg_expected_goals_non_penalty,"
            " avg_corners, avg_yellow_cards, avg_red_cards,"
            " clean_sheets, btts_matches, btts_pct)"
            " VALUES (?, ?, ?, 38, 15.2, 6.1, 58.3, 2.11, 1.87, 1.42, 0.54, 1.96,"
            " 777.7, 888.8, 999.9, 21, 17, 44.7)",
            (lid, season, tid),
        )
    # 只给 47 造 xg 档(76.0/38 = 每场被创造 2.0),87 故意没有 ——
    # 象限图"攻防"视角依赖它,缺失时必须是 None 而不是 0(补 0 会把没数据的
    # 球队画成全联赛防守最好)。两条路径都要有布景才测得到。
    conn.execute(
        "INSERT INTO fact_league_table (League_ID, Season, table_type, Team_ID, Team_Name,"
        " position, played, points, xg, xg_conceded, x_points, x_position, extra_json)"
        " VALUES (47,'2025/2026','xg',1001,'Arsenal',1,38,90,80.18,76.0,77.8,1,"
        " '{\"xPointsDiff\": 12.2, \"xPositionDiff\": 0}')"
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
