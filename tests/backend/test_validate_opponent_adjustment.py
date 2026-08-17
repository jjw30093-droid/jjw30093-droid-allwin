"""backend/scripts/validate_opponent_adjustment.py 核心逻辑测试——不重新验证
四大联赛的真实相关系数(那是脚本本身跑出来、落盘在
docs/audits/opponent-adjustment-validation-v1.json 的产物),只保证:
①收缩公式本身对(小样本对手校正力度趋近 1.0,不趋近未收缩的原始比值),
②"对手强度"计算严格 PIT(不使用目标比赛边界之后、也不使用边界当天的数据)。
"""

from __future__ import annotations

from backend.scripts.validate_opponent_adjustment import SHRINKAGE_K, _opponent_strength_as_of, _shrunk_ratio


class TestShrinkage:
    def test_zero_sample_gives_no_adjustment(self):
        """对手一场历史都没有(n=0)时,收缩后必须精确等于 1.0(完全不校正)——
        n=0 时 w=0/(0+K)=0,1+(raw-1)*0=1。"""
        assert _shrunk_ratio(raw_ratio=2.0, n=0) == 1.0

    def test_large_sample_approaches_raw_ratio(self):
        """对手历史场次远大于 K 时,收缩后应非常接近原始比值,不再被拉向 1.0。"""
        raw = 1.5
        shrunk = _shrunk_ratio(raw, n=1000)
        assert abs(shrunk - raw) < 0.02

    def test_small_sample_is_pulled_toward_one_not_raw_ratio(self):
        """典型小样本(如升班马只有 2 场历史)必须明显比大样本更接近 1.0——
        这正是"不能让升班马这类小样本对手把校正拉飞"的收缩语义。"""
        raw = 2.0
        small = _shrunk_ratio(raw, n=2)
        large = _shrunk_ratio(raw, n=100)
        assert abs(small - 1.0) < abs(large - 1.0)
        # 小样本收缩权重 w = 2/(2+8) = 0.2,shrunk = 1 + (2-1)*0.2 = 1.2
        assert abs(small - 1.2) < 1e-9

    def test_shrinkage_k_is_positive(self):
        assert SHRINKAGE_K > 0


class TestOpponentStrengthPIT:
    def test_excludes_data_on_or_after_boundary(self):
        """严格 PIT:边界当天及之后的数据一律不计入对手强度,即使日期字符串
        前缀相同——这是"目标比赛还没发生,不能用它自己或更晚的信息校正
        更早历史窗口里的同一个对手"这条纪律的直接体现。"""
        series = {
            42: [
                ("2026-01-01T10:00:00Z", 1.0),   # 边界之前,应计入
                ("2026-01-10T10:00:00Z", 5.0),   # 等于边界,不该计入
                ("2026-01-15T10:00:00Z", 9.0),   # 晚于边界,不该计入
            ]
        }
        avg, n = _opponent_strength_as_of(series, 42, "2026-01-10T10:00:00Z")
        assert n == 1
        assert avg == 1.0

    def test_respects_lookback_window(self):
        """两年前的数据不该被无限回溯拉进来。"""
        series = {42: [("2020-01-01T10:00:00Z", 99.0), ("2026-01-01T10:00:00Z", 1.0)]}
        avg, n = _opponent_strength_as_of(series, 42, "2026-06-01T10:00:00Z")
        assert n == 1
        assert avg == 1.0

    def test_unknown_opponent_returns_none(self):
        avg, n = _opponent_strength_as_of({}, 999, "2026-01-01T10:00:00Z")
        assert avg is None
        assert n == 0
