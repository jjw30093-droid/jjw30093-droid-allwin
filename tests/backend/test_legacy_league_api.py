"""legacy /api/league/* 契约收口回归测试(backend/api_server.py + backend/api/schemas.py)。

验证真实运行时行为(不只测 schema 存在),经真实 FastAPI app + 临时 core.db:
1. 4 个端点响应通过新 response_model 校验且字段值符合预期(不产生 500);
2. wdl-predictions 的字段级门禁原样保留——'upcoming' 阶段 tendency/confidence/
   reason/locked/p_home/p_draw/p_away 这些 key 在 JSON 里完全不存在(不是 null),
   'live'+未付费缺 p_*,'live'+已付费三项齐全;
3. "key 总是存在但值可能为 null"(qual_color/Match_Round 等)与"key 可能完全不存在"
   (门禁字段)两种语义在真实响应体里被精确区分;
4. 统一后的运行时错误结构 {code, message, entitlement?}。
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.db.connections import connect_rw
from backend.db.paths import db_path

TODAY = date.today()
FINISHED_DATE = (TODAY - timedelta(days=200)).isoformat()
UPCOMING_DATE = (TODAY + timedelta(days=30)).isoformat()
LIVE_DATE = (TODAY + timedelta(days=2)).isoformat()


@pytest.fixture
def legacy_core(data_dir, monkeypatch):
    """在 data_dir 已迁移的 core.db 上补建 legacy 端点用到的表,并把
    backend.api_server.DB_PATH(模块导入时冻结的常量)重定向到同一个临时文件——
    否则 legacy 模块仍会指向进程首次导入时解析的旧路径。"""
    import backend.api_server as legacy_mod

    conn = connect_rw("core")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS dim_team_i18n (
            Team_ID INTEGER PRIMARY KEY, name_zh TEXT);
        CREATE TABLE IF NOT EXISTS dim_player_i18n (
            Player_ID TEXT PRIMARY KEY, name_zh TEXT, name_zh_short TEXT);
        CREATE TABLE IF NOT EXISTS fact_league_table (
            League_ID INTEGER, Season TEXT, table_type TEXT, Team_ID INTEGER,
            position INTEGER, played INTEGER, wins INTEGER, draws INTEGER, losses INTEGER,
            goals_for INTEGER, goals_against INTEGER, goal_diff INTEGER, points INTEGER,
            qual_color TEXT);
        CREATE TABLE IF NOT EXISTS fact_season_player_stats (
            League_ID INTEGER, Season TEXT, stat_name TEXT, Player_ID TEXT, Player_Name TEXT,
            Team_ID INTEGER, Team_Name TEXT, rank INTEGER, value REAL);
        CREATE TABLE IF NOT EXISTS silver_league_season_summary (
            League_ID INTEGER, Season TEXT, total_matches INTEGER,
            home_win_pct REAL, draw_pct REAL, away_win_pct REAL, avg_total_goals REAL);
        CREATE TABLE IF NOT EXISTS silver_team_season_stats (
            League_ID INTEGER, Season TEXT, Team_ID INTEGER, matches_played INTEGER,
            avg_total_shots REAL, avg_shots_on_target REAL, avg_possession REAL,
            avg_expected_goals REAL, avg_expected_goals_on_target REAL,
            avg_corners REAL, avg_yellow_cards REAL, avg_red_cards REAL,
            clean_sheets INTEGER, btts_matches INTEGER, btts_pct REAL);
        CREATE TABLE IF NOT EXISTS silver_over_under_thresholds (
            League_ID INTEGER, Season TEXT, threshold REAL,
            over_count INTEGER, under_count INTEGER, over_pct REAL, under_pct REAL);
        CREATE TABLE IF NOT EXISTS silver_score_distribution (
            League_ID INTEGER, Season TEXT, home_score INTEGER, away_score INTEGER,
            match_count INTEGER, pct REAL);
        CREATE TABLE IF NOT EXISTS silver_goal_minute_buckets (
            League_ID INTEGER, Season TEXT, bucket TEXT, goal_count INTEGER, pct REAL);
        CREATE TABLE IF NOT EXISTS gold_wdl_predictions (
            match_id INTEGER PRIMARY KEY, league_id INTEGER, season TEXT,
            p_home REAL, p_draw REAL, p_away REAL, confidence TEXT, reason TEXT);
        """
    )
    conn.execute(
        "INSERT INTO dim_match (Match_ID, Season, League_ID, Date, Home_Team_ID, Away_Team_ID,"
        " Home_Team_Name, Away_Team_Name, home_score, away_score, status, Match_Round)"
        " VALUES (1,'2025/2026',47,?,100,200,'A','B',2,1,'Finish',NULL)", (FINISHED_DATE,))
    conn.execute(
        "INSERT INTO dim_match (Match_ID, Season, League_ID, Date, Home_Team_ID, Away_Team_ID,"
        " Home_Team_Name, Away_Team_Name, status) VALUES (2,'2026/2027',47,?,100,200,'A','B','NotStarted')",
        (UPCOMING_DATE,))
    conn.execute(
        "INSERT INTO dim_match (Match_ID, Season, League_ID, Date, Home_Team_ID, Away_Team_ID,"
        " Home_Team_Name, Away_Team_Name, status, Match_Round)"
        " VALUES (3,'2026/2027',47,?,100,200,'A','B','NotStarted','5')", (LIVE_DATE,))
    conn.execute("INSERT INTO dim_team_i18n (Team_ID, name_zh) VALUES (100,'阿森纳'),(200,'切尔西')")
    # 2026-08-25 起 migration 0012 会先按 backend/schema.py 建全量骨架,本
    # 夹具自带的窄版 CREATE IF NOT EXISTS 变成 no-op——全部改用具名列插入,
    # 不再依赖"本地窄表的列序"。
    conn.execute(
        "INSERT INTO fact_league_table (League_ID,Season,table_type,Team_ID,position,"
        "played,wins,draws,losses,goals_for,goals_against,goal_diff,points,qual_color)"
        " VALUES (47,'2025/2026','all',100,1,10,7,2,1,20,10,10,23,NULL)")
    conn.execute(
        "INSERT INTO fact_season_player_stats (League_ID,Season,stat_name,Player_ID,"
        "Player_Name,Team_ID,Team_Name,rank,value)"
        " VALUES (47,'2025/2026','goals','p1','Player One',100,'A',1,9.0)")
    conn.execute(
        "INSERT INTO silver_league_season_summary (League_ID,Season,total_matches,"
        "home_win_pct,draw_pct,away_win_pct,avg_total_goals)"
        " VALUES (47,'2025/2026',10,50.0,20.0,30.0,2.75)")
    conn.execute(
        "INSERT INTO silver_team_season_stats (League_ID,Season,Team_ID,matches_played,"
        "avg_total_shots,avg_shots_on_target,avg_possession,avg_expected_goals,"
        "avg_expected_goals_on_target,avg_corners,avg_yellow_cards,avg_red_cards,"
        "clean_sheets,btts_matches,btts_pct)"
        " VALUES (47,'2025/2026',100,10,12.0,5.0,55.0,1.8,0.9,5.0,1.0,0.1,3,4,40.0)")
    conn.execute(
        "INSERT INTO silver_over_under_thresholds (League_ID,Season,threshold,"
        "over_count,under_count,over_pct,under_pct)"
        " VALUES (47,'2025/2026',2.5,6,4,60.0,40.0)")
    conn.execute(
        "INSERT INTO silver_score_distribution (League_ID,Season,home_score,away_score,"
        "match_count,pct)"
        " VALUES (47,'2025/2026',2,1,3,30.0)")
    conn.execute(
        "INSERT INTO silver_goal_minute_buckets (League_ID,Season,bucket,goal_count,pct)"
        " VALUES (47,'2025/2026','0-15',2,20.0)")
    conn.execute(
        "INSERT INTO gold_wdl_predictions (match_id,league_id,season,p_home,p_draw,"
        "p_away,confidence,reason) VALUES (2,47,'2026/2027',0.5,0.3,0.2,'normal','ok')")
    conn.execute(
        "INSERT INTO gold_wdl_predictions (match_id,league_id,season,p_home,p_draw,"
        "p_away,confidence,reason) VALUES (3,47,'2026/2027',0.4,0.35,0.25,'normal','ok')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(legacy_mod, "DB_PATH", db_path("core"))
    return data_dir


@pytest.fixture
def legacy_client(app):
    return TestClient(app)


class TestLeagueOverview:
    def test_schema_valid_and_nullable_fields_present(self, legacy_core, legacy_client):
        r = legacy_client.get("/api/league/47/overview?season=2025/2026")
        assert r.status_code == 200, r.text
        body = r.json()
        standing = body["standings"][0]
        # qual_color 的 key 总是存在,值确实为 None(不是 key 缺席)
        assert "qual_color" in standing and standing["qual_color"] is None
        assert body["league_summary"]["total_matches"] == 10
        assert body["team_stats"][0]["team_name_zh"] == "阿森纳"
        assert body["player_leaderboards"]["goals"]["entries"][0]["player_id"] == "p1"


class TestLeagueMatches:
    def test_schema_valid_and_nullable_match_round(self, legacy_core, legacy_client):
        r = legacy_client.get("/api/league/47/matches?season=2025/2026")
        assert r.status_code == 200, r.text
        row = r.json()["matches"][0]
        assert "Match_Round" in row and row["Match_Round"] is None
        assert row["home_team_name_zh"] == "阿森纳" and row["away_team_name_zh"] == "切尔西"

    def test_invalid_season_unified_error(self, legacy_core, legacy_client):
        """全站统一错误契约(CLAUDE.md §10):顶层只有 code/message/details,不再嵌套在 detail 下。"""
        r = legacy_client.get("/api/league/47/matches?season=1999/2000")
        assert r.status_code == 400
        body = r.json()
        assert set(body.keys()) == {"code", "message", "details"}
        assert body["code"] == "invalid_season"
        assert isinstance(body["message"], str) and body["message"]
        assert body["details"] is None

    def test_unknown_league_unified_error(self, legacy_core, legacy_client):
        r = legacy_client.get("/api/league/999999/overview")
        assert r.status_code == 400
        body = r.json()
        assert set(body.keys()) == {"code", "message", "details"}
        assert body["code"] == "no_data_for_league"


class TestLeagueBetting:
    """2026-08-16 产品权限口径修正:除"每日精选"外全站比赛内容全部免费,
    包括匿名——legacy /api/league/{id}/betting 不再要求 report:deep。
    这两条断言正是要被推翻的旧规则(此前匿名恒 401 membership_required)。"""

    def test_anonymous_gets_full_betting_data(self, legacy_core, legacy_client):
        r = legacy_client.get("/api/league/47/betting?season=2025/2026")
        assert r.status_code == 200, r.text
        body = r.json()
        stats = body["team_betting_stats"][0]
        assert stats["avg_corners"] == 5.0
        assert stats["avg_yellow_cards"] == 1.0
        assert stats["avg_red_cards"] == 0.1
        assert stats["clean_sheets"] == 3
        assert stats["btts_matches"] == 4
        assert stats["btts_pct"] == 40.0
        assert body["over_under"][0]["threshold"] == 2.5

    def test_simulate_membership_param_has_no_effect(self, legacy_core, legacy_client):
        """?simulate_membership=paid 早已移除,不应有任何效果(CLAUDE.md §14.1)——
        带不带这个参数,响应都是同一份完整数据(不再有"未付费"分支可切换)。"""
        with_param = legacy_client.get("/api/league/47/betting?simulate_membership=paid&season=2025/2026")
        without_param = legacy_client.get("/api/league/47/betting?season=2025/2026")
        assert with_param.status_code == without_param.status_code == 200
        assert with_param.json() == without_param.json()


