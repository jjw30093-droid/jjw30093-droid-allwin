"""backend/queries/defensive_pressure.py 测试:图3 防守承压与限制能力聚合。"""

from __future__ import annotations

import json

from backend.db.connections import connect_rw
from backend.queries.defensive_pressure import team_defensive_pressure
from tests.backend.coreseed import insert_match, seed_core_schema

LEAGUE = 47
TEAM = 6501


def _stats(conn, match_id, team_id, **fields):
    conn.execute(
        "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
        " VALUES (?, ?, 'All', 0, ?)",
        (match_id, team_id, json.dumps(fields)),
    )


def _seed_full_window(conn, team_id, *, n=10, opp_shots=14.0, opp_sot=6.0, opp_xg=1.8, opp_box_shots=8.0):
    for j in range(n):
        mid = team_id * 100 + j
        opp = 9400 + j
        insert_match(conn, mid, league_id=LEAGUE, date=f"2025-01-{10+j:02d}",
                     home_id=team_id, away_id=opp, home="队A", away="对手",
                     status="Finish", home_score=0, away_score=1,
                     kickoff_at_utc=f"2025-01-{10+j:02d}T12:00:00Z")
        _stats(conn, mid, team_id, total_shots=5.0, ShotsOnTarget=2.0)
        _stats(conn, mid, opp, total_shots=opp_shots, ShotsOnTarget=opp_sot,
               expected_goals=opp_xg, shots_inside_box=opp_box_shots)


class TestTeamDefensivePressure:
    def test_full_window_pulls_opponent_side_values(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _seed_full_window(conn, TEAM, n=10)
        conn.commit()

        result = team_defensive_pressure(conn, TEAM, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
        assert result["tier"] == "venue_full"
        # 被射门/被射正/xGA/禁区内被射门 必须是对手的数值,不是本队自己的
        assert result["shots_faced"]["value"] == 14.0
        assert result["shots_on_target_faced"]["value"] == 6.0
        assert result["xga"]["value"] == 1.8
        assert result["box_shots_faced"]["value"] == 8.0

    def test_no_history_returns_unavailable_with_none_values(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        result = team_defensive_pressure(conn, 77777, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
        assert result["tier"] == "unavailable"
        for key in ("shots_faced", "shots_on_target_faced", "xga", "box_shots_faced"):
            assert result[key]["value"] is None
            assert result[key]["complete"] is False

    def test_partial_xga_flagged_incomplete_not_silently_zero(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        for j in range(10):
            mid = TEAM * 10 + j
            opp = 9500 + j
            insert_match(conn, mid, league_id=LEAGUE, date=f"2025-01-{10+j:02d}",
                         home_id=TEAM, away_id=opp, home="队A", away="对手",
                         status="Finish", home_score=0, away_score=1,
                         kickoff_at_utc=f"2025-01-{10+j:02d}T12:00:00Z")
            fields = dict(total_shots=13.0, ShotsOnTarget=5.0, shots_inside_box=7.0)
            if j < 8:
                fields["expected_goals"] = 1.5
            _stats(conn, mid, opp, **fields)
        conn.commit()

        result = team_defensive_pressure(conn, TEAM, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
        assert result["xga"]["complete"] is False
        assert result["xga"]["matches_with_data"] == 8
        assert result["xga"]["value"] == 1.5
        assert result["shots_faced"]["complete"] is True

    def test_does_not_expose_tackles_interceptions_or_clearances(self):
        """方案明确:拦截/解围/封堵是防守动作或风格,不是防守结果,不能进
        这张图——这条测试防止未来有人图省事把这些字段加进去。"""
        import inspect

        from backend.queries import defensive_pressure

        src = inspect.getsource(defensive_pressure.team_defensive_pressure)
        for forbidden in ("tackle", "interception", "clearance", "block"):
            assert forbidden not in src.lower()
