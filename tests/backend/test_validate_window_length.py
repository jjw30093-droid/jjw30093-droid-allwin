"""backend/scripts/validate_window_length.py 核心逻辑测试——不重新验证四大
联赛的真实相关系数(那是脚本本身跑出来、落盘在
docs/audits/window-length-validation-v1.json 的产物),只保证:
①相关系数计算本身对,②样本外切分真的把 CUTOFF 之前的比赛排除在"验证目标"外,
③两组窗口(5/10)用同一批目标才可比,不满足两组都够 10 场历史的目标要跳过。
"""

from __future__ import annotations

from backend.db.connections import connect_rw
from backend.scripts.validate_window_length import _corr, validate_league
from tests.backend.coreseed import insert_match, seed_core_schema

LEAGUE = 47
TEAM = 1001


def test_corr_perfect_positive_correlation():
    assert _corr([1, 2, 3, 4], [1, 2, 3, 4]) == 1.0


def test_corr_perfect_negative_correlation():
    assert _corr([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0


def test_corr_no_variance_returns_zero_not_crash():
    assert _corr([1, 1, 1], [1, 2, 3]) == 0.0


def _stats(conn, match_id, team_id, xg, *, opp_id):
    import json
    conn.execute(
        "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
        " VALUES (?, ?, 'All', 0, ?)",
        (match_id, team_id, json.dumps({"expected_goals": xg})),
    )
    conn.execute(
        "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
        " VALUES (?, ?, 'All', 0, ?)",
        (match_id, opp_id, json.dumps({"expected_goals": 1.0})),
    )


class TestValidateLeagueHoldoutSplit:
    def test_targets_before_cutoff_are_excluded_from_validation(self, data_dir):
        """CUTOFF 之前的比赛只能当窗口原料,不能同时也是验证目标——
        构造一个全部比赛都在 CUTOFF(2025-01-01)之前的场景,验证结果里
        validated_matches 必须是 0(insufficient_sample),不能借用调参期
        的比赛充当样本外验证。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        for i in range(15):
            mid = 9000 + i
            insert_match(conn, mid, league_id=LEAGUE, date=f"2024-01-{10+i:02d}",
                         home_id=TEAM, away_id=2000 + i, home="队A", away=f"对手{i}",
                         status="Finish", home_score=1, away_score=0)
            _stats(conn, mid, TEAM, 1.5, opp_id=2000 + i)
        conn.commit()
        r = validate_league(conn, LEAGUE)
        assert r["insufficient_sample"] is True
        assert r["validated_matches"] == 0

    def test_needs_ten_prior_games_before_target_counts(self, data_dir):
        """两组窗口(5/10)必须用同一批验证目标才可比——某个目标之前如果连
        10 场历史都凑不够,连 window=5 那组都不该用它,不能"5 场那组有效、
        10 场那组跳过"导致两组目标集合不一致。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        # 只给 8 场历史(不足 10),CUTOFF 之后再打 1 场当验证目标
        for i in range(8):
            mid = 9100 + i
            insert_match(conn, mid, league_id=LEAGUE, date=f"2024-06-{10+i:02d}",
                         home_id=TEAM, away_id=2100 + i, home="队A", away=f"对手{i}",
                         status="Finish", home_score=1, away_score=0)
            _stats(conn, mid, TEAM, 1.5, opp_id=2100 + i)
        insert_match(conn, 9200, league_id=LEAGUE, date="2025-06-01",
                     home_id=TEAM, away_id=2200, home="队A", away="验证目标对手",
                     status="Finish", home_score=1, away_score=0)
        _stats(conn, 9200, TEAM, 2.0, opp_id=2200)
        conn.commit()
        r = validate_league(conn, LEAGUE)
        # 历史只有 8 场,不满足"两组都要求凑满 10 场"的门槛,这一个目标应被跳过
        assert r["validated_matches"] == 0
