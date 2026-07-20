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
    conn.execute(
        "INSERT INTO fact_league_table VALUES (47,'2025/2026','all',100,1,10,7,2,1,20,10,10,23,NULL)")
    conn.execute(
        "INSERT INTO fact_season_player_stats"
        " VALUES (47,'2025/2026','goals','p1','Player One',100,'A',1,9.0)")
    conn.execute(
        "INSERT INTO silver_league_season_summary VALUES (47,'2025/2026',10,50.0,20.0,30.0,2.75)")
    conn.execute(
        "INSERT INTO silver_team_season_stats"
        " VALUES (47,'2025/2026',100,10,12.0,5.0,55.0,1.8,0.9,5.0,1.0,0.1,3,4,40.0)")
    conn.execute("INSERT INTO silver_over_under_thresholds VALUES (47,'2025/2026',2.5,6,4,60.0,40.0)")
    conn.execute("INSERT INTO silver_score_distribution VALUES (47,'2025/2026',2,1,3,30.0)")
    conn.execute("INSERT INTO silver_goal_minute_buckets VALUES (47,'2025/2026','0-15',2,20.0)")
    conn.execute("INSERT INTO gold_wdl_predictions VALUES (2,47,'2026/2027',0.5,0.3,0.2,'normal','ok')")
    conn.execute("INSERT INTO gold_wdl_predictions VALUES (3,47,'2026/2027',0.4,0.35,0.25,'normal','ok')")
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
    def test_anonymous_gets_unified_401(self, legacy_core, legacy_client):
        r = legacy_client.get("/api/league/47/betting?season=2025/2026")
        assert r.status_code == 401
        body = r.json()
        assert set(body.keys()) == {"code", "message", "details"}
        assert body == {
            "code": "membership_required",
            "message": "需要 report:deep 权益才能访问该数据",
            "details": {"entitlement": "report:deep"},
        }

    def test_simulate_membership_param_still_dead(self, legacy_core, legacy_client):
        """?simulate_membership=paid 早已移除,不应有任何效果(CLAUDE.md §14.1)。"""
        r = legacy_client.get("/api/league/47/betting?simulate_membership=paid")
        assert r.status_code == 401


class TestWdlPredictionsFieldGating:
    """三态判别联合(LegacyWdlUpcomingMatch / LiveLockedMatch / LiveFullMatch,
    backend/api/schemas.py):三种 JSON 形状物理上互斥(每个变体 extra=forbid),
    不是同一个宽松 dict 靠字段可选/可空掩盖差异。"""

    def test_upcoming_entry_lacks_gated_keys_entirely(self, legacy_core, legacy_client):
        r = legacy_client.get("/api/league/47/wdl-predictions?season=2026/2027")
        assert r.status_code == 200, r.text
        matches = r.json()["matches"]
        upcoming = next(m for m in matches if m["availability"] == "upcoming")
        for key in ("tendency", "confidence", "reason", "locked", "p_home", "p_draw", "p_away"):
            assert key not in upcoming, f"{key} 不应出现在 upcoming 条目里(应完全缺席,不是 null)"
        # 始终存在的字段(即便值可能为 None)确实存在
        for key in ("date", "round", "status", "home_team_name_zh", "days_until_kickoff"):
            assert key in upcoming

    def test_live_unpaid_has_tendency_but_lacks_probabilities(self, legacy_core, legacy_client):
        r = legacy_client.get("/api/league/47/wdl-predictions?season=2026/2027")
        matches = r.json()["matches"]
        live = next(m for m in matches if m["availability"] == "live")
        assert live["locked"] is True
        assert live["tendency"] == "home"
        for key in ("p_home", "p_draw", "p_away"):
            assert key not in live, f"未付费不应下发 {key}"

    def test_live_unpaid_raw_json_has_no_probability_leakage(self, legacy_core, legacy_client):
        """未付费响应的原始 JSON 文本里不得出现真实概率数值(不是放了真值再指望
        前端隐藏)。用可辨识的概率值(0.777/0.123)反证。"""
        r = legacy_client.get("/api/league/47/wdl-predictions?season=2026/2027")
        assert "0.777" not in r.text and "0.123" not in r.text

    def test_live_paid_via_monkeypatch_has_full_probabilities(
        self, legacy_core, legacy_client, monkeypatch
    ):
        """真实 HTTP 付费分支(monkeypatch require_membership=True,而非只测内部
        模型):locked=false,p_home/p_draw/p_away 三项齐全且为真实值。"""
        import backend.api_server as legacy_mod

        monkeypatch.setattr(legacy_mod, "require_membership", lambda request: True)
        r = legacy_client.get("/api/league/47/wdl-predictions?season=2026/2027")
        assert r.status_code == 200, r.text
        live = next(m for m in r.json()["matches"] if m["availability"] == "live")
        assert live["locked"] is False
        assert live["p_home"] == 0.4 and live["p_draw"] == 0.35 and live["p_away"] == 0.25
        # upcoming 分支不受付费状态影响,依旧完全缺席门禁字段
        upcoming = next(m for m in r.json()["matches"] if m["availability"] == "upcoming")
        assert not any(
            k in upcoming for k in ("tendency", "confidence", "reason", "locked",
                                    "p_home", "p_draw", "p_away")
        )

    def test_openapi_declares_three_named_variants(self, legacy_core, legacy_client, app):
        """OpenAPI 契约层面确实是三个具名模型的 Union,不是一个宽松模型。"""
        spec = app.openapi()
        comps = spec["components"]["schemas"]
        assert "LegacyWdlUpcomingMatch" in comps
        assert "LegacyWdlLiveLockedMatch" in comps
        assert "LegacyWdlLiveFullMatch" in comps
        assert comps["LegacyWdlLiveLockedMatch"]["properties"]["locked"]["const"] is True
        assert comps["LegacyWdlLiveFullMatch"]["properties"]["locked"]["const"] is False
        assert "p_home" not in comps["LegacyWdlUpcomingMatch"]["properties"]
        assert "p_home" not in comps["LegacyWdlLiveLockedMatch"]["properties"]
        assert "p_home" in comps["LegacyWdlLiveFullMatch"]["properties"]
