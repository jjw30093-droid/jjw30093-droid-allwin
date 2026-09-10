"""backend/metrics/percentile.py 纯函数测试。"""

from __future__ import annotations

from backend.metrics.percentile import (
    GAP_FLOOR,
    Highlight,
    gap_wording,
    group_percentile,
    nearest_peers,
    percentile_of,
    top_gaps,
)


class TestPercentileOf:
    def test_top_of_distribution_is_100(self):
        assert percentile_of(6.0, [1.0, 2.0, 3.0, 4.0, 5.0], lower_is_better=False) == 100

    def test_bottom_of_distribution_is_0(self):
        assert percentile_of(0.5, [1.0, 2.0, 3.0, 4.0, 5.0], lower_is_better=False) == 0

    def test_lower_is_better_flips_direction(self):
        # xGA 之类:值最小(1.0)应该拿到最高百分位。
        assert percentile_of(1.0, [2.0, 3.0, 4.0, 5.0, 6.0], lower_is_better=True) == 100
        assert percentile_of(6.0, [1.0, 2.0, 3.0, 4.0, 5.0], lower_is_better=True) == 0

    def test_none_value_returns_none(self):
        assert percentile_of(None, [1.0, 2.0, 3.0, 4.0, 5.0], lower_is_better=False) is None

    def test_insufficient_league_sample_returns_none(self):
        # 分布(不含自身)只有 4 个,< MIN_LEAGUE_SAMPLE(5) -> None,不编造。
        assert percentile_of(3.0, [1.0, 2.0, 4.0, 5.0], lower_is_better=False) is None

    def test_exactly_min_sample_computes(self):
        assert percentile_of(3.0, [1.0, 2.0, 4.0, 5.0, 6.0], lower_is_better=False) is not None

    def test_ties_do_not_crash_and_stay_in_range(self):
        pct = percentile_of(3.0, [3.0, 3.0, 3.0, 3.0, 3.0], lower_is_better=False)
        assert pct == 0  # 严格小于自身值的球队占比,全部并列时是 0


class TestGroupPercentile:
    def test_requires_min_metrics(self):
        # 只有 1 个非空百分位,< min_metrics(2) -> 整组 None,不用单指标冒充整组。
        result = group_percentile([80, None, None], min_metrics=2)
        assert result.value is None
        assert result.metrics_used == 1

    def test_averages_present_metrics_only(self):
        result = group_percentile([80, 60, None], min_metrics=2)
        assert result.value == 70.0
        assert result.metrics_used == 2


class TestTopGaps:
    def _h(self, key, gap):
        return Highlight(key=key, name_zh=key, home_percentile=50 + gap, away_percentile=50,
                          gap=gap, home_value=1.0, away_value=1.0)

    def test_filters_below_floor(self):
        rows = [self._h("a", GAP_FLOOR - 1), self._h("b", GAP_FLOOR)]
        result = top_gaps(rows)
        assert [r.key for r in result] == ["b"]

    def test_sorts_descending_and_caps_at_k(self):
        rows = [self._h("a", 20), self._h("b", 60), self._h("c", 30), self._h("d", 45)]
        result = top_gaps(rows, k=3)
        assert [r.key for r in result] == ["b", "d", "c"]


class TestGapWording:
    def test_thresholds(self):
        assert gap_wording(45) == "明显更强"
        assert gap_wording(30) == "更强一些"
        assert gap_wording(18) == "略占优势"
        assert gap_wording(5) == "基本持平"


class TestNearestPeers:
    def test_picks_two_closest_by_absolute_gap(self):
        # 目标队 85 分位;70/83/87/30 里 83 和 87 最接近(差 2、2),
        # 30 最远(差 55)——不能被"最接近"选中。
        pcts = {1: 85.0, 2: 70.0, 3: 83.0, 4: 87.0, 5: 30.0}
        result = nearest_peers(1, pcts, k=2)
        assert [p.team_id for p in result] == [3, 4]

    def test_excludes_self(self):
        pcts = {1: 50.0, 2: 51.0}
        result = nearest_peers(1, pcts, k=2)
        assert all(p.team_id != 1 for p in result)

    def test_target_without_percentile_returns_empty(self):
        pcts = {2: 50.0, 3: 60.0}
        assert nearest_peers(1, pcts, k=2) == []

    def test_fewer_than_k_candidates_returns_what_exists_not_padded(self):
        pcts = {1: 50.0, 2: 60.0}
        result = nearest_peers(1, pcts, k=2)
        assert len(result) == 1
        assert result[0].team_id == 2

    def test_ties_broken_by_team_id_for_stable_ordering(self):
        # 2 和 3 到目标(50)的距离相同(都是 10),按 team_id 升序定序,
        # 不能在两次调用之间随机抖动。
        pcts = {1: 50.0, 2: 60.0, 3: 40.0, 4: 90.0}
        result = nearest_peers(1, pcts, k=2)
        assert [p.team_id for p in result] == [2, 3]
