"""backend/queries/league_stats.py::team_season_stats 的赛季就绪门槛
(2026-09-14,站长要求):"球队数据"页(含象限图)只有当季联赛至少一支球队
完赛 4 场后才展示数据,欧冠/欧联/欧协联(CROSS_LEAGUE_LEAGUE_IDS)不受限。

与既有的 MIN_MATCHES_FOR_DEFAULT_SEASON 是两件不同的事:那个只影响"没显式
传 season 时默认选中哪一季",这个决定"不管季是怎么选出来的,这个赛季现在
够不够格展示真实数据"——用户显式 ?season= 指定一个刚开踢的新赛季照样会被
这条门槛挡住。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.db.connections import connect_rw
from backend.queries import league_stats as q

from .coreseed import insert_match, seed_core_schema

LEAGUE = 47
UCL = 42
SEASON = "2025/2026"


def _stats_row(conn, league_id, season, team_id, matches_played):
    conn.execute(
        "INSERT INTO silver_team_season_stats (League_ID, Season, Team_ID, matches_played,"
        " avg_total_shots, avg_shots_on_target, avg_possession, avg_expected_goals,"
        " avg_expected_goals_on_target, avg_expected_goals_open_play,"
        " avg_expected_goals_set_play, avg_expected_goals_non_penalty,"
        " avg_corners, avg_yellow_cards, avg_red_cards, clean_sheets, btts_matches, btts_pct)"
        " VALUES (?, ?, ?, ?, 12.0, 5.0, 50.0, 1.5, 1.3, 1.0, 0.5, 1.4, 5.0, 1.0, 0.1, 2, 1, 50.0)",
        (league_id, season, team_id, matches_played),
    )
    # dim_match 至少要有一场落在该赛季,_seasons_of 才能枚举到这个 Season。
    # 日期必须真的落在 2025/2026 赛季窗口内(英超/欧战大致 8 月~次年 5-6 月),
    # 否则会撞上 0011 迁移的赛季一致性触发器(Season 与 (League_ID, Date)
    # 推导结果对不上时 ABORT)。
    insert_match(
        conn, 900000 + team_id, league_id=league_id, season=season, date="2025-09-10",
        home_id=team_id, away_id=team_id + 500, status="Finish", home_score=1, away_score=0,
    )


class TestSeasonDataGate:
    def test_below_threshold_returns_empty_with_reason(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _stats_row(conn, LEAGUE, SEASON, 1001, matches_played=2)
        _stats_row(conn, LEAGUE, SEASON, 1002, matches_played=3)
        conn.commit()

        data = q.team_season_stats(conn, LEAGUE, SEASON)
        assert data["rows"] == []
        assert data["boards"] == []
        assert data["empty_reason"] == "本赛季刚开始，暂无足够数据（需至少一支球队完赛 4 场）"
        # available_seasons 不受影响——赛季本身依然存在、可被看见,只是数据不展示
        assert SEASON in data["available_seasons"]

    def test_at_threshold_shows_data(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _stats_row(conn, LEAGUE, SEASON, 1001, matches_played=4)
        conn.commit()

        data = q.team_season_stats(conn, LEAGUE, SEASON)
        assert len(data["rows"]) == 1
        assert "empty_reason" not in data

    def test_only_one_team_needs_to_reach_threshold(self, data_dir):
        """"联赛中有球队已完赛场次到达4场"——是"至少一支",不是"全部"。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _stats_row(conn, LEAGUE, SEASON, 1001, matches_played=1)
        _stats_row(conn, LEAGUE, SEASON, 1002, matches_played=4)
        conn.commit()

        data = q.team_season_stats(conn, LEAGUE, SEASON)
        assert len(data["rows"]) == 2  # 门槛一旦达标,该队(即使 1 场)照常展示,不逐队各自过滤

    def test_uefa_competitions_exempt_even_below_threshold(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _stats_row(conn, UCL, SEASON, 3001, matches_played=1)
        conn.commit()

        data = q.team_season_stats(conn, UCL, SEASON)
        assert len(data["rows"]) == 1
        assert "empty_reason" not in data

    def test_api_route_propagates_empty_reason(self, app, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _stats_row(conn, LEAGUE, SEASON, 1001, matches_played=1)
        conn.commit()

        client = TestClient(app)
        r = client.get(f"/api/v1/leagues/{LEAGUE}/team-stats", params={"season": SEASON})
        assert r.status_code == 200
        body = r.json()
        assert body["rows"] == []
        assert body["empty_reason"] == "本赛季刚开始，暂无足够数据（需至少一支球队完赛 4 场）"

    def test_route_generic_fallback_not_clobbered_by_specific_reason(self, app, data_dir):
        """路由层的通用兜底("该联赛暂无球队赛季统计数据")用 setdefault,
        不能覆盖查询层已经给出的更具体理由——回归 backend/api/routes_public.py
        的这处改动。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _stats_row(conn, LEAGUE, SEASON, 1001, matches_played=1)
        conn.commit()

        client = TestClient(app)
        r = client.get(f"/api/v1/leagues/{LEAGUE}/team-stats", params={"season": SEASON})
        assert "刚开始" in r.json()["empty_reason"]
        assert r.json()["empty_reason"] != "该联赛暂无球队赛季统计数据"
