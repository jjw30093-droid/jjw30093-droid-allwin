"""backend/silver/ratio_metrics.py 测试:球队象限图复合指标的赛季聚合。

配对纪律与 backend/queries/attack_chain.py::_ratio_field 同一节奏(逐场配对
后再对整个赛季求和),这里额外验证 SQL 层面的实现细节:分母<=0 剔除、
opponent 自连接、带点号 key 的取值(_ratio_field 的 '$.' || key 写法对这种
key 会静默失效,本模块必须用带引号的 JSON path)。
"""

from __future__ import annotations

import json

import pytest

from backend.db.connections import connect_rw
from backend.silver.ratio_metrics import (
    GK_SAVES_ABOVE_EXPECTED_SPECS,
    GOAL_DELTA_SPECS,
    MIXED_SHOTMAP_RATIO_SPECS,
    RATIO_SPECS,
    SHOTMAP_RATIO_SPECS,
    RatioSpec,
    _json_path,
    build_team_season_ratios,
    season_start_year,
    touches_opp_box_eligible,
)
from tests.backend.coreseed import insert_match, seed_core_schema

LEAGUE = 47
SEASON = "2025/2026"
TEAM_A = 7101
TEAM_B = 7102


def _stats(conn, match_id, team_id, *, goals=0, **fields):
    conn.execute(
        "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
        " VALUES (?, ?, 'All', ?, ?)",
        (match_id, team_id, goals, json.dumps(fields)),
    )


def _match(conn, match_id, *, home_id=TEAM_A, away_id=TEAM_B, day=10):
    insert_match(
        conn, match_id, league_id=LEAGUE, season=SEASON, date=f"2025-09-{day:02d}",
        home_id=home_id, away_id=away_id, home="队A", away="队B",
        status="Finish", home_score=1, away_score=0,
        kickoff_at_utc=f"2025-09-{day:02d}T12:00:00Z",
    )


def _shot(conn, match_id, team_id, *, situation, xg, period="FirstHalf",
          outcome=None, xgot=None):
    conn.execute(
        "INSERT INTO fact_shotmap (Match_ID, Team_ID, Period, Situation, xG, Outcome, xGOT)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (match_id, team_id, period, situation, xg, outcome, xgot),
    )


class TestJsonPath:
    def test_quotes_the_whole_key(self):
        """'$.' || key 对带点号的 key 会静默失效(点号在 JSON path 里是分隔符);
        本模块必须整体加引号。"""
        assert _json_path("matchstats.headers.tackles") == '$."matchstats.headers.tackles"'
        assert _json_path("passes") == '$."passes"'


class TestBuildTeamSeasonRatios:
    def test_pairs_within_same_match_and_sums(self, data_dir):
        """3 场比赛,分子分母都来自同一行 —— 逐场配对后求和,不是两个独立场均相除。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        for j in range(3):
            mid = TEAM_A * 10 + j
            _match(conn, mid, day=10 + j)
            _stats(conn, mid, TEAM_A, opposition_half_passes=200.0, accurate_passes=250.0)
            _stats(conn, mid, TEAM_B, opposition_half_passes=50.0, accurate_passes=200.0)
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 3, TEAM_B: 3})
        # 现在 RATIO_SPECS 有一打指标,同一支球队会产出多行——按 metric_key 过滤,
        # 不能再假设"这支球队只有一行"(早年只有 opp_half_pass_share 一个 spec
        # 时这个假设成立,加了 opp_territory_share 之后 TEAM_A 的 opponent 行
        # 也会命中,靠 Team_ID 建字典会被后来的 spec 覆盖掉)。
        row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "opp_half_pass_share")
        assert row["numerator_sum"] == 600.0  # 200*3
        assert row["denominator_sum"] == 750.0  # 250*3
        assert row["paired_matches"] == 3
        assert row["matches_played"] == 3

    def test_match_missing_either_side_excluded_from_pairing(self, data_dir):
        """一场只有分子没有分母(或反之)—— 不计入配对,不产生虚假比率,
        也不拉低/抬高其它场次算出的比例。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        for j in range(3):
            mid = TEAM_A * 10 + j
            _match(conn, mid, day=10 + j)
            _stats(conn, mid, TEAM_A, opposition_half_passes=200.0, accurate_passes=250.0)
        # 第 4 场只有分子,没有 accurate_passes —— 不该进配对
        mid = TEAM_A * 10 + 99
        _match(conn, mid, day=20)
        _stats(conn, mid, TEAM_A, opposition_half_passes=999.0)
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 4})
        row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "opp_half_pass_share")
        assert row["paired_matches"] == 3
        assert row["numerator_sum"] == 600.0
        assert row["matches_played"] == 4  # 4 场完赛,但只有 3 场配上对

    def test_zero_or_negative_denominator_excluded(self, data_dir):
        """分母 <= 0 的场次不计入配对(0/几 不是有意义的比率)。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _stats(conn, TEAM_A * 10, TEAM_A, opposition_half_passes=100.0, accurate_passes=200.0)
        _match(conn, TEAM_A * 10 + 1, day=11)
        _stats(conn, TEAM_A * 10 + 1, TEAM_A, opposition_half_passes=50.0, accurate_passes=0.0)
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 2})
        row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "opp_half_pass_share")
        assert row["paired_matches"] == 1
        assert row["numerator_sum"] == 100.0
        assert row["denominator_sum"] == 200.0

    def test_no_matches_returns_no_rows(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {})
        assert rows == []

    def test_dotted_key_extracted_correctly(self, data_dir):
        """matchstats.headers.tackles 这种带字面点号的 key —— '$.' || key 的写法
        会静默返回 NULL,本模块用带引号的 JSON path 必须能正确取出真实值。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _stats(conn, TEAM_A * 10, TEAM_A, **{"matchstats.headers.tackles": 12.0, "interceptions": 4.0})
        conn.commit()

        from backend.silver import ratio_metrics as rm

        spec = RatioSpec(
            metric_key="_test_dotted_key",
            source="self",
            numerator=("matchstats.headers.tackles",),
            denominator=("interceptions",),
            methodology_version="test",
        )
        rows = rm._rows_for_spec(conn, LEAGUE, SEASON, spec)
        assert len(rows) == 1
        team_id, num, den, paired = rows[0]
        assert team_id == TEAM_A
        assert num == 12.0
        assert den == 4.0
        assert paired == 1

    def test_multi_key_numerator_sums_within_row(self, data_dir):
        """numerator/denominator 声明多个 key 时同一行内相加(不是分别配对)。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _stats(conn, TEAM_A * 10, TEAM_A, expected_goals_open_play=0.8, expected_goals_set_play=0.3,
               expected_goals_non_penalty=1.1)
        conn.commit()

        from backend.silver import ratio_metrics as rm

        spec = RatioSpec(
            metric_key="_test_multi_key",
            source="self",
            numerator=("expected_goals_open_play", "expected_goals_set_play"),
            denominator=("expected_goals_non_penalty",),
            methodology_version="test",
        )
        rows = rm._rows_for_spec(conn, LEAGUE, SEASON, spec)
        team_id, num, den, paired = rows[0]
        assert num == 1.1  # 0.8 + 0.3(浮点误差在这个量级下不会显现)
        assert den == 1.1

    def test_opponent_source_self_joins_the_other_team_row(self, data_dir):
        """source='opponent' 取的是同场**另一队**的字段,不是自己的。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _stats(conn, TEAM_A * 10, TEAM_A, aerials_won=10.0)
        _stats(conn, TEAM_A * 10, TEAM_B, aerials_won=4.0)
        conn.commit()

        from backend.silver import ratio_metrics as rm

        spec = RatioSpec(
            metric_key="_test_opponent_aerials",
            source="opponent",
            numerator=("aerials_won",),
            denominator=("aerials_won",),
            methodology_version="test",
        )
        rows = rm._rows_for_spec(conn, LEAGUE, SEASON, spec)
        by_team = {t: (n, d) for t, n, d, _ in rows}
        # TEAM_A 的"对手值"应该是 TEAM_B 的 4.0,反之亦然
        assert by_team[TEAM_A] == (4.0, 4.0)
        assert by_team[TEAM_B] == (10.0, 10.0)

    def test_set_piece_xg_share_denominator_is_open_plus_set_play(self, data_dir):
        """站长拍板的口径:分母是运动战+定位球两项相加,不是 expected_goals_non_penalty
        字段本身——这条测试锁定 RATIO_SPECS 里的实际声明没有被改错。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _stats(conn, TEAM_A * 10, TEAM_A, expected_goals_open_play=0.9, expected_goals_set_play=0.3,
               expected_goals_non_penalty=1.3)  # 故意让 non_penalty 与 open+set 不相等
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1})
        row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "set_piece_xg_share")
        assert row["numerator_sum"] == 0.3
        assert row["denominator_sum"] == 1.2  # 0.9+0.3,不是 1.3
        assert row["paired_matches"] == 1

    def test_set_piece_xga_share_reads_opponent_row(self, data_dir):
        """失球端定位球占比取的是对手的 open_play/set_play,不是本队自己的。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _stats(conn, TEAM_A * 10, TEAM_A, expected_goals_open_play=1.0, expected_goals_set_play=1.0)
        _stats(conn, TEAM_A * 10, TEAM_B, expected_goals_open_play=0.6, expected_goals_set_play=0.2)
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1, TEAM_B: 1})
        a_row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "set_piece_xga_share")
        assert a_row["numerator_sum"] == 0.2  # TEAM_B(对手)的 set_play
        assert a_row["denominator_sum"] == 0.8  # TEAM_B 的 0.6+0.2

    def test_shot_accuracy_denominator_equals_total_minus_blocked(self, data_dir):
        """射正率分母用 ShotsOnTarget+ShotsOffTarget 两项相加,等价于
        total_shots-blocked_shots(不在 SQL 里做减法)——这条测试同时验证
        "两项相加"这个替代写法本身算对了。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _stats(conn, TEAM_A * 10, TEAM_A, ShotsOnTarget=5.0, ShotsOffTarget=4.0, blocked_shots=3.0,
               total_shots=12.0)  # 5+4+3=12,与 total_shots 一致(真实数据里恒成立)
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1})
        row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "shot_accuracy")
        assert row["numerator_sum"] == 5.0
        assert row["denominator_sum"] == 9.0  # 5+4,即 total_shots(12) - blocked_shots(3)

    def test_box_shot_share_pairs_inside_and_outside(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _stats(conn, TEAM_A * 10, TEAM_A, shots_inside_box=8.0, shots_outside_box=4.0)
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1})
        row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "box_shot_share")
        assert row["numerator_sum"] == 8.0
        assert row["denominator_sum"] == 12.0

    def test_opp_xg_per_shot_reads_opponent_row(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _stats(conn, TEAM_A * 10, TEAM_A, expected_goals=1.5, total_shots=10.0)
        _stats(conn, TEAM_A * 10, TEAM_B, expected_goals=0.9, total_shots=8.0)
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1, TEAM_B: 1})
        row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "opp_xg_per_shot")
        assert row["numerator_sum"] == 0.9  # TEAM_B(对手)的 xG,不是自己的 1.5
        assert row["denominator_sum"] == 8.0

    def test_all_ratio_specs_run_without_error_on_empty_data(self, data_dir):
        """RATIO_SPECS 里任何一条,在完全没有匹配数据时都应该安静返回空,
        不抛异常——新增 spec 时最容易漏测的边界。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {})
        assert rows == []
        assert len(RATIO_SPECS) >= 3  # 锁定本次新增的两条确实进了列表

    def test_duplicate_call_is_idempotent(self, data_dir):
        """同一批数据调两次,返回结果完全一致(分区幂等,配合 build_silver 的
        DELETE+INSERT 节奏)。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _stats(conn, TEAM_A * 10, TEAM_A, opposition_half_passes=100.0, accurate_passes=200.0)
        conn.commit()

        rows1 = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1})
        rows2 = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1})
        assert rows1 == rows2


class TestNumeratorMinus:
    """绝佳机会把握率:分子是"大机会 − 错失大机会",不在 SQL 里报错,
    而是 _diff_expr 把减法直接写进表达式。"""

    def test_subtracts_within_row(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _stats(conn, TEAM_A * 10, TEAM_A, big_chance=9.0, big_chance_missed_title=4.0)
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1})
        row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "big_chance_conversion")
        assert row["numerator_sum"] == 5.0  # 9 - 4
        assert row["denominator_sum"] == 9.0  # 分母就是 big_chance 本身,不减

    def test_missing_minus_key_excludes_the_row_not_treated_as_zero(self, data_dir):
        """减数那一侧缺失时,整场应该被排除(NULL 参与减法结果仍是 NULL),
        不能被静默当 0 减,那样会把"缺失"误算成"全部把握住了"。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _stats(conn, TEAM_A * 10, TEAM_A, big_chance=9.0)  # 缺 big_chance_missed_title
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1})
        row = next(
            (r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "big_chance_conversion"),
            None,
        )
        assert row is None  # 唯一一场缺数据,没有任何配对成功的行


class TestSelfPlusOpponentSource:
    """争顶优势占比:分子只取本队,分母是"本队+对手"同名字段之和。"""

    def test_denominator_sums_both_teams(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _stats(conn, TEAM_A * 10, TEAM_A, aerials_won=12.0)
        _stats(conn, TEAM_A * 10, TEAM_B, aerials_won=8.0)
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1, TEAM_B: 1})
        a_row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "aerial_win_share")
        b_row = next(r for r in rows if r["Team_ID"] == TEAM_B and r["metric_key"] == "aerial_win_share")
        assert a_row["numerator_sum"] == 12.0
        assert a_row["denominator_sum"] == 20.0  # 12+8,两队共用同一个分母
        assert b_row["numerator_sum"] == 8.0
        assert b_row["denominator_sum"] == 20.0  # 分母相同,分子不同——两队争顶优势占比互补


class TestShotmapSpecs:
    """快速反击 xG 占比:从 fact_shotmap 按 Situation 聚合,分母是全部非点球 xG。"""

    def test_numerator_only_sums_matching_situation(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _shot(conn, TEAM_A * 10, TEAM_A, situation="FastBreak", xg=0.3)
        _shot(conn, TEAM_A * 10, TEAM_A, situation="RegularPlay", xg=0.2)
        _shot(conn, TEAM_A * 10, TEAM_A, situation="SetPiece", xg=0.1)
        conn.commit()

        spec = SHOTMAP_RATIO_SPECS[0]
        assert spec.metric_key == "fast_break_xg_share"
        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1})
        row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "fast_break_xg_share")
        assert row["numerator_sum"] == 0.3
        assert row["denominator_sum"] == pytest.approx(0.6)  # 0.3+0.2+0.1,全部非点球

    def test_denominator_excludes_penalty(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _shot(conn, TEAM_A * 10, TEAM_A, situation="FastBreak", xg=0.3)
        _shot(conn, TEAM_A * 10, TEAM_A, situation="Penalty", xg=0.7884)
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1})
        row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "fast_break_xg_share")
        assert row["denominator_sum"] == 0.3  # 点球那 0.7884 不计入分母

    def test_own_goal_null_xg_shot_skipped_not_treated_as_zero(self, data_dir):
        """xG 为 NULL 的射门(乌龙球)不计入分子分母,不静默当 0。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _shot(conn, TEAM_A * 10, TEAM_A, situation="FastBreak", xg=0.3)
        _shot(conn, TEAM_A * 10, TEAM_A, situation="RegularPlay", xg=None)  # 乌龙球
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1})
        row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "fast_break_xg_share")
        assert row["denominator_sum"] == 0.3  # 乌龙球那一脚(xG=NULL)不计入

    def test_zero_shots_in_a_match_excluded(self, data_dir):
        """该队该场一脚都没射(den=0)—— 不产出该场的配对,不是 0/0。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _match(conn, TEAM_A * 10 + 1, day=11)
        _shot(conn, TEAM_A * 10, TEAM_A, situation="FastBreak", xg=0.3)
        # 第二场 TEAM_A 一脚未射(无 shotmap 行)
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 2})
        row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "fast_break_xg_share")
        assert row["paired_matches"] == 1  # 只有第一场配对成功

    def test_shotmap_spec_metric_key_registered(self, data_dir):
        """ShotmapRatioSpec 同样要求先在 registry 登记才能用,规则和 RatioSpec 一致。"""
        from backend.metrics.registry import get_metric

        for spec in SHOTMAP_RATIO_SPECS:
            m = get_metric(spec.metric_key)
            assert m.denominator is not None


class TestEligibleSeasons:
    """touches_opp_box 系列指标(box_touch_share/npxg_per_box_touch)只在覆盖率
    100% 的两个赛季产出行,其它赛季完全不产出——不是"产出后前端隐藏",
    是数据源本身在那些赛季不可比,silver 层就不该写这行。"""

    def test_no_rows_for_ineligible_season(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        ineligible_season = "2021/2022"
        insert_match(
            conn, TEAM_A * 10, league_id=LEAGUE, season=ineligible_season, date="2021-09-10",
            home_id=TEAM_A, away_id=TEAM_B, home="队A", away="队B",
            status="Finish", home_score=1, away_score=0, kickoff_at_utc="2021-09-10T12:00:00Z",
        )
        _stats(conn, TEAM_A * 10, TEAM_A, touches_opp_box=20.0)
        _stats(conn, TEAM_A * 10, TEAM_B, touches_opp_box=15.0)
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, ineligible_season, {TEAM_A: 1, TEAM_B: 1})
        # fixture 只给了 touches_opp_box,其它指标需要的字段全部缺失,本来就
        # 产不出行;这里单独确认 box_touch_share/npxg_per_box_touch 这两个
        # 受季别限制的 key 一行都没有——即使数据本身是有的(touches_opp_box
        # 确实赋了值),也因为季别不在白名单里被 spec 层面整个跳过。
        assert [r for r in rows if r["metric_key"] in ("box_touch_share", "npxg_per_box_touch")] == []

    def test_rows_produced_for_eligible_season(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)  # 默认 SEASON = "2025/2026",覆盖率 100%
        _stats(conn, TEAM_A * 10, TEAM_A, touches_opp_box=20.0, expected_goals_non_penalty=1.5)
        _stats(conn, TEAM_A * 10, TEAM_B, touches_opp_box=15.0)
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1, TEAM_B: 1})
        box_row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "box_touch_share")
        assert box_row["numerator_sum"] == 20.0
        assert box_row["denominator_sum"] == 35.0  # 20+15
        npxg_row = next(
            r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "npxg_per_box_touch"
        )
        assert npxg_row["numerator_sum"] == 1.5
        assert npxg_row["denominator_sum"] == 20.0


class TestTouchesOppBoxEligibleIsYearBased:
    """2026-09-16 真实反馈:站长追问"技术只会变多不会变少,26-27赛季这个
    字段怎么会没有"——根因是当年只把审计到的两个赛季('2024/2025',
    '2025/2026')硬编码进 frozenset,后续每个新赛季('2026','2026/2027'……)
    都会被这份清单卡住,尽管生产实测覆盖率同样是 98%+。改成"起始年份 >= 2024"
    的判定式后,这里锁死新赛季不需要再改代码就能正确判定为 eligible。"""

    @pytest.mark.parametrize(
        "season,expected_year",
        [
            ("2024", 2024),
            ("2024/2025", 2024),
            ("2025", 2025),
            ("2025/2026", 2025),
            ("2026", 2026),
            ("2026/2027", 2026),
            ("2020/2021", 2020),
        ],
    )
    def test_season_start_year_parses_both_formats(self, season, expected_year):
        assert season_start_year(season) == expected_year

    def test_season_start_year_returns_none_for_unparseable_input(self):
        assert season_start_year("") is None
        assert season_start_year("abcd/efgh") is None

    @pytest.mark.parametrize(
        "season",
        ["2024", "2024/2025", "2025", "2025/2026", "2026", "2026/2027", "2030/2031"],
    )
    def test_eligible_from_2024_onward_regardless_of_format(self, season):
        assert touches_opp_box_eligible(season) is True

    @pytest.mark.parametrize("season", ["2020/2021", "2021/2022", "2022/2023", "2023/2024"])
    def test_ineligible_before_2024(self, season):
        assert touches_opp_box_eligible(season) is False


class TestSelfOverOpponentSource:
    """防守动作密度:分子只取本队,分母独立取对手(不同队、不同字段,不相加,
    与 self_plus_opponent 的"两队同名字段相加"是两回事)。"""

    def test_numerator_from_self_denominator_from_opponent(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _stats(
            conn, TEAM_A * 10, TEAM_A,
            **{"matchstats.headers.tackles": 15.0, "interceptions": 10.0, "fouls": 8.0, "passes": 999.0},
        )
        _stats(conn, TEAM_A * 10, TEAM_B, passes=400.0)
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1, TEAM_B: 1})
        row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "def_action_density")
        assert row["numerator_sum"] == 33.0  # 15+10+8,本队
        assert row["denominator_sum"] == 400.0  # 对手的 passes,不是本队的 999


class TestMixedShotmapSpec:
    """角球成射率:分子来自 fact_shotmap 的射门次数(按 Situation 计次,不是
    xG 求和),分母来自 fact_team_match_stats.extra_json 的 corners——第三种
    聚合形状,跨表按 (Match_ID, Team_ID) 配对。"""

    def test_numerator_counts_shots_not_xg(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _shot(conn, TEAM_A * 10, TEAM_A, situation="FromCorner", xg=0.9)
        _shot(conn, TEAM_A * 10, TEAM_A, situation="FromCorner", xg=0.01)
        _shot(conn, TEAM_A * 10, TEAM_A, situation="RegularPlay", xg=0.3)
        _stats(conn, TEAM_A * 10, TEAM_A, corners=8.0)
        conn.commit()

        spec = MIXED_SHOTMAP_RATIO_SPECS[0]
        assert spec.metric_key == "corner_shot_rate"
        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1})
        row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "corner_shot_rate")
        assert row["numerator_sum"] == 2  # 两脚角球射门,不是 xG 之和(0.91)
        assert row["denominator_sum"] == 8.0

    def test_zero_corner_shots_still_counts_as_zero(self, data_dir):
        """该场角球数存在但一脚角球射门都没有——分子合法为 0,不是缺失。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _stats(conn, TEAM_A * 10, TEAM_A, corners=5.0)
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1})
        row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "corner_shot_rate")
        assert row["numerator_sum"] == 0
        assert row["denominator_sum"] == 5.0
        assert row["paired_matches"] == 1

    def test_missing_corners_excludes_match(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _shot(conn, TEAM_A * 10, TEAM_A, situation="FromCorner", xg=0.3)
        _stats(conn, TEAM_A * 10, TEAM_A)  # 没有 corners 字段
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1})
        row = next((r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "corner_shot_rate"), None)
        assert row is None  # 唯一一场也缺分母,整个不产出行


class TestGoalDeltaSpec:
    """场均终结超额:(非点球进球 − 非点球xG) / 场次。非点球进球口径必须走
    fact_team_match_stats.Goals(核心列) − 点球进球数,不走 shotmap 全量的
    乌龙球归属推断——见 backend/silver/ratio_metrics.py::_non_penalty_goals_expr
    的真实案例注释(shotmap 里存在 Outcome='Goal' 但未真正计分的行,推断法
    会算错,Goals 核心列不会)。"""

    def test_goals_minus_npxg(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _stats(conn, TEAM_A * 10, TEAM_A, goals=2, expected_goals_non_penalty=1.2)
        conn.commit()

        spec = GOAL_DELTA_SPECS[0]
        assert spec.metric_key == "finishing_delta"
        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1})
        row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "finishing_delta")
        assert row["numerator_sum"] == pytest.approx(0.8)  # 2 - 1.2
        assert row["denominator_sum"] == 1  # 场均展示的分母就是场次数

    def test_penalty_goal_is_subtracted_via_goals_core_column(self, data_dir):
        """核心列 Goals=3(含 1 个点球),必须减掉点球进球数,不能把点球也
        算进"非点球进球"。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _stats(conn, TEAM_A * 10, TEAM_A, goals=3, expected_goals_non_penalty=1.0)
        _shot(conn, TEAM_A * 10, TEAM_A, situation="Penalty", xg=0.7884, outcome="Goal")
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1})
        row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "finishing_delta")
        assert row["numerator_sum"] == pytest.approx(1.0)  # (3-1) - 1.0,不是 3-1.0

    def test_shotmap_outcome_goal_without_matching_official_score_is_ignored(self, data_dir):
        """真实审计发现的场景(2026-09-14):shotmap 里有一条 Outcome='Goal'、
        xG 非 NULL 的射门,但 Goals 核心列(=官方比分)显示该队一球未进——
        大概率是 VAR 吹掉的进球,射门事件类型仍标记 Goal 但没有真的计分。
        必须按 Goals 核心列为准,不能被这条 shotmap 行带偏。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _stats(conn, TEAM_A * 10, TEAM_A, goals=0, expected_goals_non_penalty=0.4)
        _shot(conn, TEAM_A * 10, TEAM_A, situation="RegularPlay", xg=0.0, outcome="Goal")
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1})
        row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "finishing_delta")
        assert row["numerator_sum"] == pytest.approx(-0.4)  # 0 - 0.4,不是 1 - 0.4

    def test_sample_count_is_non_penalty_shot_count(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _stats(conn, TEAM_A * 10, TEAM_A, goals=1, expected_goals_non_penalty=0.5)
        _shot(conn, TEAM_A * 10, TEAM_A, situation="RegularPlay", xg=0.3, outcome="Goal")
        _shot(conn, TEAM_A * 10, TEAM_A, situation="SetPiece", xg=0.1, outcome="Miss")
        _shot(conn, TEAM_A * 10, TEAM_A, situation="Penalty", xg=0.7884, outcome="Goal")
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1})
        row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "finishing_delta")
        assert row["sample_count"] == 2  # 两脚非点球射门,点球那脚不计入

    def test_missing_npxg_excludes_match(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _stats(conn, TEAM_A * 10, TEAM_A, goals=1)  # 没有 expected_goals_non_penalty
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1})
        row = next((r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "finishing_delta"), None)
        assert row is None


class TestGkSavesAboveExpectedSpec:
    """场均门将扑救超额:对手射正(非点球、非乌龙、xGOT 非空)Σ xGOT − 非点球
    失球。"""

    def test_sums_opponent_xgot_minus_goals_conceded(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _stats(conn, TEAM_A * 10, TEAM_A, goals=0)
        _stats(conn, TEAM_A * 10, TEAM_B, goals=1)  # 对手进 1 球,是 TEAM_A 的非点球失球
        _shot(conn, TEAM_A * 10, TEAM_B, situation="RegularPlay", xg=0.4, xgot=0.5, outcome="Goal")
        _shot(conn, TEAM_A * 10, TEAM_B, situation="RegularPlay", xg=0.2, xgot=0.3, outcome="AttemptSaved")
        conn.commit()

        spec = GK_SAVES_ABOVE_EXPECTED_SPECS[0]
        assert spec.metric_key == "gk_saves_above_expected"
        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1, TEAM_B: 1})
        row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "gk_saves_above_expected")
        assert row["numerator_sum"] == pytest.approx(-0.2)  # (0.5+0.3) - 1

    def test_excludes_penalty_and_missing_xgot_and_own_shots(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _stats(conn, TEAM_A * 10, TEAM_A, goals=0)
        _stats(conn, TEAM_A * 10, TEAM_B, goals=0)
        _shot(conn, TEAM_A * 10, TEAM_B, situation="RegularPlay", xg=0.3, xgot=0.4, outcome="AttemptSaved")
        _shot(conn, TEAM_A * 10, TEAM_B, situation="Penalty", xg=0.7884, xgot=0.8, outcome="AttemptSaved")
        _shot(conn, TEAM_A * 10, TEAM_B, situation="RegularPlay", xg=0.2, xgot=None, outcome="AttemptSaved")
        _shot(conn, TEAM_A * 10, TEAM_A, situation="RegularPlay", xg=0.5, xgot=0.6, outcome="AttemptSaved")
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1, TEAM_B: 1})
        row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "gk_saves_above_expected")
        assert row["numerator_sum"] == pytest.approx(0.4)  # 只有第一脚有效:0.4-0
        assert row["sample_count"] == 1

    def test_match_excluded_when_no_valid_xgot_shots(self, data_dir):
        """对手射正全部缺 xGOT——整场排除,不按 0 计入(否则会把缺失读成
        零预期失球,系统性夸大扑救超额)。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _stats(conn, TEAM_A * 10, TEAM_A, goals=0)
        _stats(conn, TEAM_A * 10, TEAM_B, goals=0)
        _shot(conn, TEAM_A * 10, TEAM_B, situation="RegularPlay", xg=0.3, xgot=None, outcome="AttemptSaved")
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1, TEAM_B: 1})
        row = next((r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "gk_saves_above_expected"), None)
        assert row is None

    def test_zero_shots_faced_is_legitimately_included(self, data_dir):
        """对手确实一次射正都没有(不是数据缺失,qualifying_count=0)——合法
        计入为 0,不能跟"有射正但 xGOT 全缺失"那种真正的缺失混淆(两者都会
        让 valid_count=0,必须另外看 qualifying_count 才分得清)。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _match(conn, TEAM_A * 10, day=10)
        _stats(conn, TEAM_A * 10, TEAM_A, goals=0)
        _stats(conn, TEAM_A * 10, TEAM_B, goals=0)
        conn.commit()

        rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM_A: 1, TEAM_B: 1})
        row = next(r for r in rows if r["Team_ID"] == TEAM_A and r["metric_key"] == "gk_saves_above_expected")
        assert row["numerator_sum"] == pytest.approx(0.0)  # 0 次射正 → 0 - 0(非点球失球也是0)
        assert row["sample_count"] == 0

    def test_gk_saves_above_expected_metric_key_registered(self, data_dir):
        from backend.metrics.registry import get_metric

        for spec in GK_SAVES_ABOVE_EXPECTED_SPECS:
            m = get_metric(spec.metric_key)
            assert m.denominator is not None
