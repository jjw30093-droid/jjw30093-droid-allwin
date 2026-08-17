"""backend/queries/venue_baseline.py 测试:主客场分离的联赛百分位基准。"""

from __future__ import annotations

import json

from backend.db.connections import connect_rw
from backend.queries.venue_baseline import MIN_LEAGUE_SAMPLE, league_percentile
from tests.backend.coreseed import insert_match, seed_core_schema

LEAGUE = 47


def _stats(conn, match_id, team_id, **fields):
    conn.execute(
        "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
        " VALUES (?, ?, 'All', 0, ?)",
        (match_id, team_id, json.dumps(fields)),
    )


def _seed_home_team(conn, team_id, xg_value, *, n=10, start_day=1):
    for j in range(n):
        mid = team_id * 100 + j
        day = start_day + j
        insert_match(conn, mid, league_id=LEAGUE, date=f"2025-01-{day:02d}",
                     home_id=team_id, away_id=9000 + j, home=f"队{team_id}", away="路人",
                     status="Finish", home_score=1, away_score=0,
                     kickoff_at_utc=f"2025-01-{day:02d}T12:00:00Z")
        _stats(conn, mid, team_id, expected_goals=xg_value)
        _stats(conn, mid, 9000 + j, expected_goals=1.0)


class TestLeaguePercentile:
    def test_percentile_reflects_rank_within_same_venue_distribution(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        # 6 支球队(target + 5 others),主场场均 xG 严格递增:1.0..6.0
        target = 2001
        for i, tid in enumerate((2002, 2003, 2004, 2005, 2006, target)):
            _seed_home_team(conn, tid, float(i + 1))
        conn.commit()

        result = league_percentile(conn, target, LEAGUE, "2025-02-01T00:00:00Z", "expected_goals", is_home=True)
        # target 的值(6.0)是全场最高,5 支对照队全部低于它 -> 5/5 = 100%
        assert result.percentile == 100
        assert result.value == 6.0
        assert result.league_sample_size == 5

    def test_home_and_away_use_independent_distributions(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        target = 3001
        # 主场:target 垫底(1.0),其余五队更高
        for i, tid in enumerate((3002, 3003, 3004, 3005, 3006, target)):
            _seed_home_team(conn, tid, float(6 - i) if tid != target else 1.0)
        conn.commit()

        home_result = league_percentile(conn, target, LEAGUE, "2025-02-01T00:00:00Z", "expected_goals", is_home=True)
        away_result = league_percentile(conn, target, LEAGUE, "2025-02-01T00:00:00Z", "expected_goals", is_home=False)
        # target 从未打过客场,客场分布里它自己都没有值 -> percentile None
        assert home_result.percentile == 0
        assert away_result.percentile is None

    def test_insufficient_league_sample_returns_none_not_fabricated(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        target = 4001
        # 联赛里只有 target 和另外 2 支球队(< MIN_LEAGUE_SAMPLE),不足以给百分位
        assert MIN_LEAGUE_SAMPLE >= 3
        for tid in (4002, 4003, target):
            _seed_home_team(conn, tid, 3.0)
        conn.commit()

        result = league_percentile(conn, target, LEAGUE, "2025-02-01T00:00:00Z", "expected_goals", is_home=True)
        assert result.percentile is None
        assert result.league_sample_size == 2

    def test_own_value_missing_returns_none(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        for tid in (5002, 5003, 5004, 5005, 5006, 5007):
            _seed_home_team(conn, tid, 3.0)
        conn.commit()
        # target(5001)从未在这个联赛主场出现过
        result = league_percentile(conn, 5001, LEAGUE, "2025-02-01T00:00:00Z", "expected_goals", is_home=True)
        assert result.value is None
        assert result.percentile is None

    def test_future_matches_excluded_from_league_distribution(self, data_dir):
        """PIT 安全:边界之后才踢的比赛不该被算进联赛分布(否则用还没发生的
        比赛"预测"当前场景,是未来信息泄漏)。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        target = 6001
        for i, tid in enumerate((6002, 6003, 6004, 6005, 6006, target)):
            _seed_home_team(conn, tid, float(i + 1))
        # 6002 未来(边界之后)再踢一场离谱高 xG,不该改变它已经确定的历史均值排名
        insert_match(conn, 660299, league_id=LEAGUE, date="2025-06-01",
                     home_id=6002, away_id=9999, home="队6002", away="路人",
                     status="Finish", home_score=5, away_score=0,
                     kickoff_at_utc="2025-06-01T12:00:00Z")
        _stats(conn, 660299, 6002, expected_goals=99.0)
        _stats(conn, 660299, 9999, expected_goals=0.0)
        conn.commit()

        result = league_percentile(conn, target, LEAGUE, "2025-02-01T00:00:00Z", "expected_goals", is_home=True)
        assert result.percentile == 100  # 结果不受边界之后那场离谱数据影响


class TestMixedTierExcludedFromDistribution:
    """验收返工三:mixed(该队自己同场景样本不足、退回混合主客场)算出来的
    均值,口径已经和 venue_full/venue_partial 的"纯同场景均值"不一样,
    不能被悄悄混进主场/客场百分位分布——那会用一个不同口径的数字污染
    "同场景比较"这个前提。"""

    def test_reference_team_with_mixed_tier_does_not_enter_distribution(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        target = 7001
        # 5 支"干净"的同场景对照队(真实主场历史 >= min_n,tier=venue_full)
        for i, tid in enumerate((7002, 7003, 7004, 7005, 7006)):
            _seed_home_team(conn, tid, float([1, 2, 4, 5, 6][i]))
        # target 自己也是干净主场历史,均值 3.0,理应排在 {1,2} 之上、{4,5,6} 之下
        _seed_home_team(conn, target, 3.0)
        # 污染源 R:主场历史只有 3 场(< DEFAULT_MIN_N=5),真实 tier 会退化成
        # mixed(合并主客场)。故意把主场那 3 场 xG 设成极端值 1000,
        # 混合后均值仍然高达 300——如果这个 mixed 均值被当成"主场"分布的
        # 一员,会把 target 的百分位错误拉低。
        r = 7099
        for j in range(3):
            mid = r * 100 + j
            insert_match(conn, mid, league_id=LEAGUE, date=f"2025-01-{10+j:02d}",
                         home_id=r, away_id=9500 + j, home="污染队", away="路人",
                         status="Finish", home_score=1, away_score=0,
                         kickoff_at_utc=f"2025-01-{10+j:02d}T12:00:00Z")
            _stats(conn, mid, r, expected_goals=1000.0)
            _stats(conn, mid, 9500 + j, expected_goals=1.0)
        # R 的客场对手固定复用同一支队(9600),使它自己攒够 7 场主场历史
        # (>= min_n,真实 tier=venue_partial,应当正常进入分布)——避免每场
        # 客场比赛都临时造一支新队,导致分布里混进一堆各自只踢过 1 场、
        # 同样是 mixed 档位的噪声队,干扰这条测试真正要验证的东西。
        for j in range(7):
            mid = r * 100 + 50 + j
            insert_match(conn, mid, league_id=LEAGUE, date=f"2025-01-{20+j:02d}",
                         home_id=9600, away_id=r, home="路人2", away="污染队",
                         status="Finish", home_score=0, away_score=1,
                         kickoff_at_utc=f"2025-01-{20+j:02d}T12:00:00Z")
            _stats(conn, mid, r, expected_goals=0.0)
            _stats(conn, mid, 9600, expected_goals=1.0)
        conn.commit()

        result = league_percentile(conn, target, LEAGUE, "2025-02-01T00:00:00Z", "expected_goals", is_home=True)
        # 修复后:R 的 mixed 均值(300)不进入分布。分布 = 5 支干净对照队
        # {1,2,4,5,6} + 9600(真实 7 场主场历史,venue_partial,值 1.0) = 6 支,
        # target(3.0)高于其中 {1,2,1} 三支 -> 3/6 = 50%。
        assert result.league_sample_size == 6
        assert result.percentile == 50
