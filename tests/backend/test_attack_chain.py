"""backend/queries/attack_chain.py 测试:图1 进攻转化链聚合。"""

from __future__ import annotations

import json

from backend.db.connections import connect_rw
from backend.queries.attack_chain import team_attack_chain
from tests.backend.coreseed import insert_match, seed_core_schema

LEAGUE = 47
TEAM = 7001


def _stats(conn, match_id, team_id, **fields):
    conn.execute(
        "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
        " VALUES (?, ?, 'All', 0, ?)",
        (match_id, team_id, json.dumps(fields)),
    )


def _seed_full_window(conn, team_id, *, n=10, xg_all=True):
    for j in range(n):
        mid = team_id * 100 + j
        insert_match(conn, mid, league_id=LEAGUE, date=f"2025-01-{10+j:02d}",
                     home_id=team_id, away_id=9000 + j, home="队A", away="路人",
                     status="Finish", home_score=1, away_score=0,
                     kickoff_at_utc=f"2025-01-{10+j:02d}T12:00:00Z")
        fields = dict(
            opposition_half_passes=200.0, passes=400.0, touches_opp_box=20.0,
            total_shots=12.0, ShotsOnTarget=5.0,
        )
        if xg_all or j < n - 2:
            fields["expected_goals"] = 1.5
            fields["expected_goals_on_target"] = 1.2
        _stats(conn, mid, team_id, **fields)


class TestTeamAttackChain:
    def test_full_window_all_fields_complete(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _seed_full_window(conn, TEAM, n=10, xg_all=True)
        conn.commit()

        result = team_attack_chain(conn, TEAM, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
        assert result["tier"] == "venue_full"
        assert result["matches"] == 10
        assert result["opp_half_pass_share"]["value"] == 50.0  # 200/400
        assert result["opp_half_pass_share"]["complete"] is True
        assert result["xg"]["value"] == 1.5
        assert result["xg"]["complete"] is True
        assert result["xgot"]["value"] == 1.2
        assert result["shots"]["value"] == 12.0
        assert result["shots_on_target"]["value"] == 5.0
        assert result["touches_opp_box"]["value"] == 20.0

    def test_partial_xg_flagged_incomplete_not_silently_zero(self, data_dir):
        """两场缺 xG——不能被当 0 参与平均,也不能假装完整。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _seed_full_window(conn, TEAM, n=10, xg_all=False)
        conn.commit()

        result = team_attack_chain(conn, TEAM, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
        assert result["xg"]["complete"] is False
        assert result["xg"]["matches_with_data"] == 8
        assert result["xg"]["value"] == 1.5  # 均值仍然只用有值的 8 场,不是被 0 拉低
        # 非 xG 字段每场都有,不应受 xG 缺失影响
        assert result["shots"]["complete"] is True

    def test_no_history_returns_unavailable_tier_and_none_values(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        result = team_attack_chain(conn, 99999, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
        assert result["tier"] == "unavailable"
        assert result["matches"] == 0
        assert result["xg"]["value"] is None
        assert result["xg"]["complete"] is False

    def test_ratio_uses_paired_matches_only(self, data_dir):
        """分子分母必须来自同一批场次——某场只有分子没有分母(或反之)时,
        那场不该计入比例分母求和,否则比例会失真。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        # 3 场都有 opposition_half_passes + passes(200/400 = 50%)
        for j in range(3):
            mid = TEAM * 10 + j
            insert_match(conn, mid, league_id=LEAGUE, date=f"2025-01-{10+j:02d}",
                         home_id=TEAM, away_id=9000 + j, home="队A", away="路人",
                         status="Finish", home_score=1, away_score=0,
                         kickoff_at_utc=f"2025-01-{10+j:02d}T12:00:00Z")
            _stats(conn, mid, TEAM, opposition_half_passes=200.0, passes=400.0)
        # 1 场只有 opposition_half_passes,没有 passes(不该拉低或改变比例)
        mid = TEAM * 10 + 99
        insert_match(conn, mid, league_id=LEAGUE, date="2025-01-20",
                     home_id=TEAM, away_id=9099, home="队A", away="路人",
                     status="Finish", home_score=1, away_score=0,
                     kickoff_at_utc="2025-01-20T12:00:00Z")
        _stats(conn, mid, TEAM, opposition_half_passes=999.0)
        conn.commit()

        result = team_attack_chain(conn, TEAM, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
        assert result["opp_half_pass_share"]["value"] == 50.0
        assert result["opp_half_pass_share"]["matches_with_data"] == 3


class TestConversionMetrics:
    """验收返工一:进攻转化链此前只有 6 项独立场均产量,不含任何转化率——
    这里的四个比率必须来自同一批比赛的同行配对相除,不是两个独立均值相除。"""

    def test_full_window_conversion_rates_computed_from_paired_rows(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _seed_full_window(conn, TEAM, n=10, xg_all=True)
        conn.commit()

        result = team_attack_chain(conn, TEAM, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
        # 每场:touches_opp_box=20, total_shots=12, ShotsOnTarget=5, xg=1.5, xgot=1.2
        assert result["shots_per_100_box_touches"]["value"] == 60.0  # 12/20*100
        assert result["shot_on_target_rate"]["value"] == round(100 * 5 / 12, 1)
        assert result["xg_per_shot"]["value"] == round(1.5 / 12, 3)
        assert result["xgot_per_sot"]["value"] == round(1.2 / 5, 3)

    def test_zero_denominator_returns_none_not_fabricated_zero(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        for j in range(5):
            mid = TEAM * 10 + j
            insert_match(conn, mid, league_id=LEAGUE, date=f"2025-01-{10+j:02d}",
                         home_id=TEAM, away_id=9200 + j, home="队A", away="路人",
                         status="Finish", home_score=1, away_score=0,
                         kickoff_at_utc=f"2025-01-{10+j:02d}T12:00:00Z")
            # 射门为 0 的场次:射正率/每脚xG 分母为 0,必须诚实给 None
            _stats(conn, mid, TEAM, total_shots=0.0, ShotsOnTarget=0.0, expected_goals=0.0, touches_opp_box=10.0)
        conn.commit()

        result = team_attack_chain(conn, TEAM, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
        assert result["shot_on_target_rate"]["value"] is None
        assert result["xg_per_shot"]["value"] is None

    def test_conversion_uses_same_match_pairing_not_two_independent_averages(self, data_dir):
        """场次 A 只有分子(shots)没有分母(touches_opp_box),场次 B 反过来——
        若错误地各自独立求均值再相除,会用不对应的场次拼出一个假比率;
        正确实现应该只用两个字段都存在的那批场次。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        mid_a = TEAM * 10 + 1
        insert_match(conn, mid_a, league_id=LEAGUE, date="2025-01-10",
                     home_id=TEAM, away_id=9300, home="队A", away="路人",
                     status="Finish", home_score=1, away_score=0,
                     kickoff_at_utc="2025-01-10T12:00:00Z")
        _stats(conn, mid_a, TEAM, total_shots=100.0)  # 没有 touches_opp_box
        mid_b = TEAM * 10 + 2
        insert_match(conn, mid_b, league_id=LEAGUE, date="2025-01-11",
                     home_id=TEAM, away_id=9301, home="队A", away="路人",
                     status="Finish", home_score=1, away_score=0,
                     kickoff_at_utc="2025-01-11T12:00:00Z")
        _stats(conn, mid_b, TEAM, touches_opp_box=1.0)  # 没有 total_shots
        mid_c = TEAM * 10 + 3
        insert_match(conn, mid_c, league_id=LEAGUE, date="2025-01-12",
                     home_id=TEAM, away_id=9302, home="队A", away="路人",
                     status="Finish", home_score=1, away_score=0,
                     kickoff_at_utc="2025-01-12T12:00:00Z")
        _stats(conn, mid_c, TEAM, total_shots=6.0, touches_opp_box=12.0)  # 唯一配对场次
        conn.commit()

        result = team_attack_chain(conn, TEAM, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
        # 若错误地独立均值相除:sum(shots)=106/1场有值=106,sum(box)=13/1场有值=13,
        # 106/13*100≈815——明显不对。正确答案只用配对场次 mid_c:6/12*100=50。
        assert result["shots_per_100_box_touches"]["value"] == 50.0
        assert result["shots_per_100_box_touches"]["matches_with_data"] == 1

    def test_conversion_metrics_carry_coverage_evidence(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _seed_full_window(conn, TEAM, n=10, xg_all=False)  # 最后两场缺 xG
        conn.commit()

        result = team_attack_chain(conn, TEAM, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
        assert result["xg_per_shot"]["complete"] is False
        assert result["xg_per_shot"]["matches_with_data"] == 8
        assert result["shot_on_target_rate"]["complete"] is True  # 不受 xG 缺失影响

    def test_conversion_metrics_grouped_separately_from_volume_metrics(self, data_dir):
        """手机端要求明确分成"进攻产量"和"转化效率"两组——后端用
        `volume_keys`/`conversion_keys` 显式声明分组,前端不用自己猜哪个
        字段属于哪一组。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _seed_full_window(conn, TEAM, n=10, xg_all=True)
        conn.commit()

        result = team_attack_chain(conn, TEAM, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
        assert set(result["volume_keys"]) == {
            "opp_half_pass_share", "touches_opp_box", "shots", "shots_on_target", "xg", "xgot",
        }
        assert set(result["conversion_keys"]) == {
            "shots_per_100_box_touches", "shot_on_target_rate", "xg_per_shot", "xgot_per_sot",
        }
