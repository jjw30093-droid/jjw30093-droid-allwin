"""backend/queries/opponent_adjust.py 测试:PIT-safe 对手强度查询 + 收缩校正,
用 backend/scripts/validate_opponent_adjustment.py 已验证通过的两类公式
(进攻/防守),不测未通过验证、本模块也没提供的比例类。"""

from __future__ import annotations

import json

from backend.db.connections import connect_rw
from backend.queries import opponent_adjust as oa
from tests.backend.coreseed import insert_match, seed_core_schema

LEAGUE = 47
TEAM = 1001


def _stats(conn, match_id, team_id, **fields):
    conn.execute(
        "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
        " VALUES (?, ?, 'All', 0, ?)",
        (match_id, team_id, json.dumps(fields)),
    )


class TestShrunkRatio:
    def test_zero_sample_gives_exactly_one(self):
        assert oa.shrunk_ratio(3.0, 0) == 1.0

    def test_large_sample_close_to_raw(self):
        assert abs(oa.shrunk_ratio(1.4, 500) - 1.4) < 0.01


class TestAdjustAttackWindow:
    def test_strong_defense_opponent_scales_own_xg_up(self, data_dir):
        """对手历史让出 xG 很少(防守强)——本队打进这种对手的那场 xG 校正后
        应该被放大(因为原始值相对"打了个软柿子"的样本更有含金量)。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        # 联赛里另外两支球队(制造联赛平均基准),场均让出 xG = 2.0
        for i, opp in enumerate((5001, 5002)):
            for j in range(10):
                mid = 8000 + i * 10 + j
                insert_match(conn, mid, league_id=LEAGUE, date=f"2025-01-{10+j:02d}",
                             home_id=opp, away_id=9000 + j, home=f"基准{i}", away="路人",
                             status="Finish", home_score=0, away_score=0,
                             kickoff_at_utc=f"2025-01-{10+j:02d}T12:00:00Z")
                _stats(conn, mid, opp, expected_goals=2.0)
                _stats(conn, mid, 9000 + j, expected_goals=2.0)
        # 强防守对手(TEAM 唯一对手):近两年场均只让出 0.5 xG
        strong_def_opp = 6001
        for j in range(10):
            mid = 8500 + j
            insert_match(conn, mid, league_id=LEAGUE, date=f"2025-02-{10+j:02d}",
                         home_id=strong_def_opp, away_id=9100 + j, home="强防守", away="路人",
                         status="Finish", home_score=0, away_score=0,
                         kickoff_at_utc=f"2025-02-{10+j:02d}T12:00:00Z")
            _stats(conn, mid, strong_def_opp, expected_goals=0.5)
            _stats(conn, mid, 9100 + j, expected_goals=0.5)
        # TEAM 打了强防守对手一场,自己创造 1.0 xG
        target_mid = 8600
        insert_match(conn, target_mid, league_id=LEAGUE, date="2025-03-01",
                     home_id=TEAM, away_id=strong_def_opp, home="队A", away="强防守",
                     status="Finish", home_score=1, away_score=0,
                     kickoff_at_utc="2025-03-01T12:00:00Z")
        _stats(conn, target_mid, TEAM, expected_goals=1.0)
        conn.commit()

        window_rows = [{"opp": strong_def_opp, "boundary": "2025-03-01T12:00:00Z", "value": 1.0}]
        adjusted = oa.adjust_attack_window(conn, LEAGUE, "2025-03-02T00:00:00Z", window_rows)
        # 联赛场均让出 ≈ (2.0*20 + 0.5*10)/30 ≈ 1.5;对手让出 0.5 → ratio_raw = 3.0,
        # 10 场历史收缩后 w=10/18≈0.556,shrunk = 1+(3-1)*0.556≈2.11,
        # 校正后 ≈ 1.0*2.11 = 2.11 > 原始 1.0
        assert adjusted[0] > 1.0

    def test_opponent_with_no_history_leaves_value_unadjusted(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        insert_match(conn, 8700, league_id=LEAGUE, date="2025-01-10",
                     home_id=TEAM, away_id=9999, home="队A", away="全新对手",
                     status="Finish", home_score=1, away_score=0,
                     kickoff_at_utc="2025-01-10T12:00:00Z")
        _stats(conn, 8700, TEAM, expected_goals=1.3)
        conn.commit()
        window_rows = [{"opp": 9999, "boundary": "2025-01-10T12:00:00Z", "value": 1.3}]
        adjusted = oa.adjust_attack_window(conn, LEAGUE, "2025-01-11T00:00:00Z", window_rows)
        assert adjusted[0] == 1.3  # 对手没有任何历史,原样返回,不瞎猜


class TestPitSafety:
    def test_opponent_strength_never_uses_data_at_or_after_boundary(self, data_dir):
        """对手在"边界之后"打出的离谱数据(场均让出 20 个 xG)不能泄漏进校正——
        校正结果必须完全不受这场未来比赛影响。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        opp = 7001
        insert_match(conn, 8800, league_id=LEAGUE, date="2025-01-05",
                     home_id=opp, away_id=9500, home="对手", away="路人",
                     status="Finish", home_score=0, away_score=0,
                     kickoff_at_utc="2025-01-05T12:00:00Z")
        _stats(conn, 8800, opp, expected_goals=1.0)
        _stats(conn, 9500, opp, expected_goals=1.0)
        # 边界之后的"未来"比赛,该对手离谱地让出 20 xG——不该被用到
        insert_match(conn, 8801, league_id=LEAGUE, date="2025-06-01",
                     home_id=opp, away_id=9501, home="对手", away="路人2",
                     status="Finish", home_score=0, away_score=0,
                     kickoff_at_utc="2025-06-01T12:00:00Z")
        _stats(conn, 8801, opp, expected_goals=20.0)
        conn.commit()
        window_rows = [{"opp": opp, "boundary": "2025-02-01T00:00:00Z", "value": 1.5}]
        adjusted = oa.adjust_attack_window(conn, LEAGUE, "2025-02-02T00:00:00Z", window_rows)
        # 如果未来那场泄漏进来,对手强度会被拉到 (1+20)/2=10.5,校正比值会
        # 接近 0(联赛均值远小于对手让出值),从而把 1.5 大幅缩小；
        # 正确实现应只看到边界前的那场(让出 1.0),校正比值应显著大于 0.5。
        assert adjusted[0] > 0.5


class TestNoRatioAdjustmentShipped:
    def test_module_does_not_expose_a_possession_or_ratio_adjustment_function(self):
        """样本外验证证明比例类(控球率等)校正会让预测变差(见
        docs/audits/opponent-adjustment-validation-v1.json 的 style_possession
        结论),本模块因此故意不提供任何比例类校正函数——这条测试防止未来
        有人图省事,在没有重新验证的情况下往这个模块里加一个。"""
        exported = {name for name in dir(oa) if not name.startswith("_")}
        forbidden_substrings = ("ratio", "possession", "style")
        offenders = [
            name for name in exported
            if callable(getattr(oa, name))
            and any(s in name.lower() for s in forbidden_substrings)
            and name != "shrunk_ratio"
        ]
        assert offenders == []
