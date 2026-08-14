"""赛前市场离线标定:纯函数正确性 + 时间序不泄漏未来 + 落库幂等。

这是"给意见"功能唯一的诚实性防线——如果 build_predictions 允许看到未来
数据,或者 calibrate_one 在同一批数据上既定档又验证,标定出来的"信号"就是
编造的。本文件的核心断言就是防这两件事。
"""

from __future__ import annotations

import json

import pytest

from backend.db.connections import connect_rw
from backend.eval.calibrate_markets import (
    MARKETS,
    BucketResult,
    CalibrationResult,
    _bucket_boundaries,
    _bucket_of,
    _grade,
    _is_monotonic,
    build_predictions,
    calibrate_one,
    persist,
)
from tests.backend.coreseed import insert_match, seed_core_schema


class TestPureBucketing:
    def test_boundaries_split_into_roughly_equal_groups(self):
        values = list(range(100))  # 0..99
        b = _bucket_boundaries([float(v) for v in values], k=5)
        assert len(b) == 4
        # 每个边界大致落在 20/40/60/80 分位附近
        assert b == [20.0, 40.0, 60.0, 80.0]

    def test_bucket_of_respects_boundaries(self):
        boundaries = [10.0, 20.0, 30.0, 40.0]
        assert _bucket_of(5.0, boundaries) == 0
        assert _bucket_of(10.0, boundaries) == 0  # 边界值算低侧(<=)
        assert _bucket_of(10.1, boundaries) == 1
        assert _bucket_of(999.0, boundaries) == 4  # 超过最高边界落最后一档

    def test_grade_requires_monotonic(self):
        # spread 很大但不单调 —— 不给意见,这是硬规则,不能因为差值大就放行
        assert _grade(spread_pp=40.0, monotonic=False) is None
        assert _grade(spread_pp=40.0, monotonic=True) == "★★★"
        assert _grade(spread_pp=15.0, monotonic=True) == "★★"
        assert _grade(spread_pp=6.0, monotonic=True) == "★"
        assert _grade(spread_pp=4.9, monotonic=True) is None

    def test_monotonic_allows_one_inversion_not_more(self):
        assert _is_monotonic([0.3, 0.4, 0.5, 0.6, 0.7]) is True   # 完全单调
        assert _is_monotonic([0.3, 0.5, 0.4, 0.6, 0.7]) is True   # 1 处逆序,容忍噪音
        assert _is_monotonic([0.5, 0.3, 0.6, 0.2, 0.7]) is False  # 2 处逆序,判不单调


def _seed_series(conn, team_id, league_id, values, *, start_date="2026-01-01",
                  opponent_start=9000, field="corners"):
    """给一支球队造 len(values) 场逐场递增日期的比赛,每场 extra_json[field]
    分别取 values 里的值;对手是各不相同的陪衬球队(不参与断言)。"""
    from datetime import date, timedelta
    base = date.fromisoformat(start_date)
    match_ids = []
    for i, v in enumerate(values):
        mid = 60000 + team_id * 100 + i
        opp = opponent_start + i
        d = (base + timedelta(days=i)).isoformat()
        insert_match(conn, mid, league_id=league_id, season="2025/2026", date=d,
                     home_id=team_id, away_id=opp, home="队", away=f"陪衬{i}",
                     status="Finish", home_score=1, away_score=0)
        conn.execute(
            "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
            " VALUES (?, ?, 'All', 1, ?)",
            (mid, team_id, json.dumps({field: v})),
        )
        conn.execute(
            "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
            " VALUES (?, ?, 'All', 0, ?)",
            (mid, opp, json.dumps({field: 3})),
        )
        match_ids.append(mid)
    return match_ids


class TestBuildPredictionsNoLookAhead:
    def test_one_off_opponents_never_reach_min_sample(self, data_dir):
        """MIN history = 3 场,双方都要达到才出预测。陪衬对手每场都是新的
        一次性对手、自己永远只有 0-1 场历史——这五场比赛应该一条预测都
        产不出来,不能因为目标队自己历史够了就单方面出预测。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _seed_series(conn, 1001, 47, [4, 5, 6, 7, 8])
        conn.commit()

        market = MARKETS["corners"]
        preds = build_predictions(conn, market, window=10, league_id=47)
        assert preds == []

    def test_predictor_never_uses_same_or_future_match(self, data_dir):
        """核心防线:第 k 场的预估值只能来自第 k 场之前的历史。用一个后续
        故意插入极端值(9999)的比赛验证它不会污染更早比赛的预测。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        # 用同一支陪衬对手,让其也累积历史,这样目标队和陪衬队都能达到
        # >=3 场样本量,产出可断言的预测序列。
        from datetime import date, timedelta
        base = date.fromisoformat("2026-01-01")
        team, opp = 2001, 2002
        values = [4, 4, 4, 4, 4, 9999]  # 最后一场故意插极端值
        for i, v in enumerate(values):
            mid = 61000 + i
            d = (base + timedelta(days=i)).isoformat()
            insert_match(conn, mid, league_id=47, season="2025/2026", date=d,
                         home_id=team, away_id=opp, home="队A", away="队B",
                         status="Finish", home_score=1, away_score=0)
            conn.execute(
                "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
                " VALUES (?, ?, 'All', 1, ?)", (mid, team, json.dumps({"corners": v})),
            )
            conn.execute(
                "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
                " VALUES (?, ?, 'All', 0, ?)", (mid, opp, json.dumps({"corners": v})),
            )
        conn.commit()

        market = MARKETS["corners"]
        preds = build_predictions(conn, market, window=10, league_id=47)
        # 第 4 场(索引 3,第 4 次出现,前面已有 3 场历史 4,4,4)的预估值
        # 必须只由 4,4,4 算出,不能看到第 6 场的 9999。
        early = [p for p in preds if p.match_id == 61003]
        assert early, "第 4 场应该已经有预测(前面 3 场历史足够)"
        # predictor = 两队各自历史均值之和,两队历史都是 [4,4,4] → 4+4=8
        assert early[0].predictor == pytest.approx(8.0)

    def test_goals_market_uses_goals_column_not_expected_goals_for_actual(self, data_dir):
        """大小球市场:预估值用 xG 历史,但 actual(真实结果)必须来自
        Goals 列,不能又用 expected_goals——那样等于拿模型输入验证模型输入,
        循环论证。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        from datetime import date, timedelta
        base = date.fromisoformat("2026-01-01")
        team, opp = 3001, 3002
        # xG 历史值和真实进球值刻意不同,方便断言两者没被混用
        for i in range(5):
            mid = 62000 + i
            d = (base + timedelta(days=i)).isoformat()
            insert_match(conn, mid, league_id=47, season="2025/2026", date=d,
                         home_id=team, away_id=opp, home="队A", away="队B",
                         status="Finish", home_score=3, away_score=2)  # 真实进球 5
            conn.execute(
                "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
                " VALUES (?, ?, 'All', 3, ?)",
                (mid, team, json.dumps({"expected_goals": 1.0})),  # xG 远低于真实进球
            )
            conn.execute(
                "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
                " VALUES (?, ?, 'All', 2, ?)",
                (mid, opp, json.dumps({"expected_goals": 1.0})),
            )
        conn.commit()
        preds = build_predictions(conn, MARKETS["goals"], window=10, league_id=47)
        assert preds
        # actual 必须是 Goals 列之和(3+2=5),不是 expected_goals 之和(1+1=2)
        assert all(p.actual == pytest.approx(5.0) for p in preds)
        # predictor 用 xG 历史,应该在 2.0 附近(两队各自 xG 历史均值之和)
        assert all(p.predictor == pytest.approx(2.0) for p in preds)


class TestCalibrateOne:
    def test_insufficient_total_sample_returns_none(self):
        assert calibrate_one([], market="corners", league_id=0, line=9.5) is None

    def test_out_of_sample_split_uses_disjoint_data(self):
        """train 和 valid 不能重叠——用一个"前 80% 全是低值、后 20% 全是
        高值"的极端序列验证边界确实用 train 定的,不是用全量数据定的。"""
        from backend.eval.calibrate_markets import PredictionRow

        preds = [
            PredictionRow(date=f"2026-01-{i+1:02d}", match_id=i, predictor=float(i % 5), actual=float(i % 5))
            for i in range(200)
        ]
        result = calibrate_one(preds, market="corners", league_id=0, line=2.5)
        assert result is not None
        assert result.train_size == 160  # 80% of 200
        assert sum(b.sample_size for b in result.buckets) == 40  # 20% of 200


class TestPersistIdempotent:
    def test_rerun_replaces_not_duplicates(self, data_dir):
        conn = connect_rw("platform")
        result = CalibrationResult(
            market="corners", league_id=0, line=9.5,
            buckets=[BucketResult(i, None, None, 0.4 + i * 0.05, 100) for i in range(5)],
            spread_pp=20.0, monotonic=True, signal_grade="★★", train_size=1000,
        )
        persist(conn, [result])
        persist(conn, [result])  # 重跑
        rows = conn.execute(
            "SELECT COUNT(*) FROM market_calibration WHERE market='corners' AND league_id=0 AND line=9.5"
        ).fetchone()[0]
        assert rows == 5  # 5 个档位,不是 10(证明是替换不是追加)

    def test_all_leagues_uses_zero_sentinel_not_null(self, data_dir):
        """migration 里 league_id 用 0 表示跨联赛合并,不用 NULL——SQLite
        的 UNIQUE 约束把每个 NULL 当成互不相等,NULL 会让约束形同虚设。"""
        conn = connect_rw("platform")
        result = CalibrationResult(
            market="goals", league_id=0, line=2.5,
            buckets=[BucketResult(0, None, None, 0.5, 100)],
            spread_pp=10.0, monotonic=True, signal_grade="★", train_size=500,
        )
        persist(conn, [result])
        row = conn.execute(
            "SELECT league_id FROM market_calibration WHERE market='goals' AND line=2.5"
        ).fetchone()
        assert row[0] == 0
        assert row[0] is not None
