"""backend/queries/possession_control.py 测试:图2 控球与场面控制聚合。"""

from __future__ import annotations

import json

from backend.db.connections import connect_rw
from backend.queries.possession_control import team_possession_control
from tests.backend.coreseed import insert_match, seed_core_schema

LEAGUE = 47
TEAM = 8001


def _stats(conn, match_id, team_id, **fields):
    conn.execute(
        "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
        " VALUES (?, ?, 'All', 0, ?)",
        (match_id, team_id, json.dumps(fields)),
    )


def _seed_full_window(conn, team_id, *, n=10):
    for j in range(n):
        mid = team_id * 100 + j
        insert_match(conn, mid, league_id=LEAGUE, date=f"2025-01-{10+j:02d}",
                     home_id=team_id, away_id=9200 + j, home="队A", away="路人",
                     status="Finish", home_score=1, away_score=0,
                     kickoff_at_utc=f"2025-01-{10+j:02d}T12:00:00Z")
        _stats(conn, mid, team_id, BallPossesion=55.0, accurate_passes=320.0, passes=400.0,
               opposition_half_passes=180.0, touches_opp_box=19.5)


class TestTeamPossessionControl:
    def test_full_window_computes_all_four_metrics(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _seed_full_window(conn, TEAM, n=10)
        conn.commit()

        result = team_possession_control(conn, TEAM, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
        assert result["tier"] == "venue_full"
        assert result["possession"]["value"] == 55.0
        assert result["pass_accuracy"]["value"] == 80.0  # 320/400
        assert result["opp_half_pass_share"]["value"] == 45.0  # 180/400
        assert result["touches_opp_box"]["value"] == 19.5

    def test_no_history_returns_unavailable_with_none_values(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        result = team_possession_control(conn, 88888, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
        assert result["tier"] == "unavailable"
        for key in ("possession", "pass_accuracy", "opp_half_pass_share", "touches_opp_box"):
            assert result[key]["value"] is None
            assert result[key]["complete"] is False

    def test_missing_possession_field_not_treated_as_zero(self, data_dir):
        """某场缺 BallPossesion 字段——均值只用有数据的场次算,不把缺失当 0。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        for j in range(5):
            mid = TEAM * 10 + j
            insert_match(conn, mid, league_id=LEAGUE, date=f"2025-01-{10+j:02d}",
                         home_id=TEAM, away_id=9300 + j, home="队A", away="路人",
                         status="Finish", home_score=1, away_score=0,
                         kickoff_at_utc=f"2025-01-{10+j:02d}T12:00:00Z")
            fields = dict(accurate_passes=300.0, passes=380.0, opposition_half_passes=150.0, touches_opp_box=18.0)
            if j != 0:
                fields["BallPossesion"] = 60.0
            _stats(conn, mid, TEAM, **fields)
        conn.commit()

        result = team_possession_control(conn, TEAM, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
        assert result["possession"]["value"] == 60.0
        assert result["possession"]["complete"] is False
        assert result["possession"]["matches_with_data"] == 4
