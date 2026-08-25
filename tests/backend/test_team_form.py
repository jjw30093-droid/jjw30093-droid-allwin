"""team_recent_profile:赛前市场卡的地基聚合层。

背景:所有赛后事实表(fact_team_match_stats 等)对未开赛比赛精确为 0 行,
唯一能给赛前页用的"这场比赛特有数据"是两队各自的历史聚合——本文件验证
这个聚合本身的正确性:不补 0、不跨联赛泄漏、样本量各指标独立计算。
"""

from __future__ import annotations

import json

import pytest

from backend.db.connections import connect_rw
from backend.queries.team_form import team_recent_profile
from tests.backend.coreseed import insert_match, seed_core_schema


def _seed_stats(conn, match_id, team_id, *, corners=None, yellow_cards=None,
                 touches_opp_box=None, period="All"):
    fields = {}
    if corners is not None:
        fields["corners"] = corners
    if yellow_cards is not None:
        fields["yellow_cards"] = yellow_cards
    if touches_opp_box is not None:
        fields["touches_opp_box"] = touches_opp_box
    conn.execute(
        "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
        " VALUES (?, ?, ?, 0, ?)",
        (match_id, team_id, period, json.dumps(fields)),
    )


@pytest.fixture
def five_matches(data_dir):
    """阿队(1001)近 5 场英超,对手各不相同,角球/黄牌/对手禁区触球逐场不同
    (故意让 touches_opp_box 只在 3 场里出现,验证样本量各指标独立)。"""
    conn = connect_rw("core")
    seed_core_schema(conn)
    for i, (mid, corners, yc, tob) in enumerate([
        (7101, 6, 1, 20),
        (7102, 4, 2, None),   # 这场 touches_opp_box 缺失(真实库里 ~11% 缺)
        (7103, 8, 0, 25),
        (7104, 3, 3, None),   # 又一场缺失
        (7105, 5, 1, 30),
    ]):
        insert_match(conn, mid, league_id=47, date=f"2026-01-{10+i:02d}", home_id=1001, away_id=2000+i,
                     home="阿队", away=f"对手{i}", status="Finish",
                     home_score=1, away_score=0)
        _seed_stats(conn, mid, 1001, corners=corners, yellow_cards=yc, touches_opp_box=tob)
        _seed_stats(conn, mid, 2000 + i, corners=corners - 1, yellow_cards=yc + 1)
    # 目标比赛之后的一场(不应被纳入"近期")
    insert_match(conn, 7200, league_id=47, date="2026-02-01",
                 home_id=1001, away_id=2099, home="阿队", away="未来对手",
                 status="Finish", home_score=2, away_score=0)
    _seed_stats(conn, 7200, 1001, corners=99, yellow_cards=99)
    conn.commit()
    conn.close()
    yield


class TestTeamRecentProfile:
    def test_averages_and_before_date_boundary(self, five_matches):
        conn = connect_rw("core")
        r = team_recent_profile(
            conn, 1001, before_date="2026-01-20", n=10, scope="same_league", league_id=47,
        )
        assert r["matches_considered"] == 5
        # (6+4+8+3+5)/5 = 5.2 —— 且 2026-02-01 那场(角球 99)不能混进来,
        # 否则均值会被拉到完全不同的数量级,一眼就能看出"未来数据泄漏"这个 bug
        assert r["metrics"]["corners"]["for"]["avg"] == 5.2
        assert r["metrics"]["corners"]["for"]["n"] == 5

    def test_missing_field_not_filled_with_zero(self, five_matches):
        """touches_opp_box 只在 3/5 场有数据——样本量必须是 3,不是 5,
        均值必须只用那 3 场算,不能把缺失的 2 场当 0 拉低均值。"""
        conn = connect_rw("core")
        r = team_recent_profile(
            conn, 1001, before_date="2026-01-20", n=10, scope="same_league", league_id=47,
        )
        tob = r["metrics"]["touches_opp_box"]["for"]
        assert tob["n"] == 3
        assert tob["avg"] == pytest.approx((20 + 25 + 30) / 3)

    def test_for_and_against_are_opponent_rows_not_same_team(self, five_matches):
        conn = connect_rw("core")
        r = team_recent_profile(
            conn, 1001, before_date="2026-01-20", n=10, scope="same_league", league_id=47,
        )
        # against = 对手角球均值 = (5+3+7+2+4)/5 = 4.2,不等于 for 的 5.2
        against = r["metrics"]["corners"]["against"]
        assert against["avg"] == pytest.approx(4.2)
        assert against["avg"] != r["metrics"]["corners"]["for"]["avg"]

    def test_sample_below_minimum_returns_none_not_fake_average(self, five_matches):
        """只有 2 场数据时(未达 MIN_SAMPLE=3),avg 必须是 None,不能用 2 场
        撑出一个看似正常的均值——2 场的均值波动太大,不该被当作可用信号。"""
        conn = connect_rw("core")
        r = team_recent_profile(
            conn, 1001, before_date="2026-01-20", n=10, scope="same_league", league_id=47,
        )
        tob = r["metrics"]["touches_opp_box"]["for"]
        assert tob["n"] == 3  # 刚好等于 MIN_SAMPLE,应该有值
        assert tob["avg"] is not None

        # 再造一个只出现 2 场的场景验证真正 < MIN_SAMPLE 的情况
        conn2 = connect_rw("core")
        insert_match(conn2, 7301, league_id=87, date="2026-01-11",
                     home_id=5001, away_id=5002, home="丙队", away="丁队",
                     status="Finish", home_score=1, away_score=1)
        insert_match(conn2, 7302, league_id=87, date="2026-01-15",
                     home_id=5001, away_id=5003, home="丙队", away="戊队",
                     status="Finish", home_score=0, away_score=0)
        _seed_stats(conn2, 7301, 5001, corners=6)
        _seed_stats(conn2, 7302, 5001, corners=8)
        conn2.commit()
        r2 = team_recent_profile(
            conn2, 5001, before_date="2026-01-20", n=10, scope="same_league", league_id=87,
        )
        assert r2["metrics"]["corners"]["for"]["n"] == 2
        assert r2["metrics"]["corners"]["for"]["avg"] is None

    def test_scope_same_league_excludes_other_leagues(self, five_matches):
        """same_league 只看阿队在英超(47)的比赛;跨联赛的比赛不该混入,
        否则会把不同强度联赛的角球数据错误地平均在一起。"""
        conn = connect_rw("core")
        insert_match(conn, 7400, league_id=87, date="2026-01-05",
                     home_id=1001, away_id=3000, home="阿队", away="西甲对手",
                     status="Finish", home_score=1, away_score=1)
        _seed_stats(conn, 7400, 1001, corners=1)  # 极端值,如果泄漏进来均值会明显偏低
        conn.commit()
        r = team_recent_profile(
            conn, 1001, before_date="2026-01-20", n=10, scope="same_league", league_id=47,
        )
        assert r["matches_considered"] == 5  # 不是 6
        assert r["metrics"]["corners"]["for"]["avg"] == 5.2  # 没被那场 1 拉低

    def test_no_history_league_degrades_to_empty_not_error(self):
        """英冠/荷甲/葡超/巴甲这类完全没有历史事实表的联赛,球队没有任何
        fact_team_match_stats 记录——必须优雅返回全 None,不能抛异常
        (前端据此渲染"该联赛历史数据补采中",不是让页面 500)。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        conn.commit()
        r = team_recent_profile(
            conn, 88888, before_date="2026-01-20", n=10, scope="same_league", league_id=48,
        )
        assert r["matches_considered"] == 0
        assert r["metrics"]["corners"] == {"for": {"avg": None, "n": 0}, "against": {"avg": None, "n": 0}}

    def test_period_first_half_reads_half_split(self, five_matches):
        """半场数据是单独的 Period 行,不是全场数据打折——必须真的查询
        FirstHalf 那一行,不能默默退回全场数字。"""
        conn = connect_rw("core")
        _seed_stats(conn, 7101, 1001, corners=2, period="FirstHalf")
        _seed_stats(conn, 7103, 1001, corners=3, period="FirstHalf")
        _seed_stats(conn, 7105, 1001, corners=1, period="FirstHalf")
        conn.commit()
        r = team_recent_profile(
            conn, 1001, before_date="2026-01-20", n=10, scope="same_league",
            league_id=47, period="FirstHalf",
        )
        # 只有 3 场半场数据(7101/7103/7105),不是全场的 5 场
        assert r["metrics"]["corners"]["for"]["n"] == 3
        assert r["metrics"]["corners"]["for"]["avg"] == pytest.approx((2 + 3 + 1) / 3)
