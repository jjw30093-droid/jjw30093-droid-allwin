"""联赛名次 rank_of(2026-10-02 站长:页面"第 77 分位"改写"联赛第 N")。"""

from backend.metrics.percentile import MIN_LEAGUE_SAMPLE, percentile_of, rank_of


def test_rank_higher_better_and_ties():
    others = [1.0, 2.0, 2.0, 3.0, 4.0]
    assert rank_of(5.0, others, lower_is_better=False) == 1
    assert rank_of(2.5, others, lower_is_better=False) == 3   # 3.0、4.0 比它好
    assert rank_of(2.0, others, lower_is_better=False) == 3   # 并列:只算严格更好的
    assert rank_of(0.5, others, lower_is_better=False) == 6


def test_rank_lower_is_better_direction():
    others = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert rank_of(0.5, others, lower_is_better=True) == 1
    assert rank_of(4.5, others, lower_is_better=True) == 5


def test_rank_none_exactly_when_percentile_none():
    few = [1.0] * (MIN_LEAGUE_SAMPLE - 1)
    assert rank_of(1.0, few, lower_is_better=False) is None
    assert percentile_of(1.0, few, lower_is_better=False) is None
    assert rank_of(None, [1.0] * 10, lower_is_better=False) is None


def test_rank_consistent_with_percentile_order():
    others = [0.8, 1.1, 1.3, 1.6, 1.9, 2.2, 2.4]
    vals = [0.5, 1.2, 1.7, 2.0, 2.5]
    pcts = [percentile_of(v, others, lower_is_better=False) for v in vals]
    ranks = [rank_of(v, others, lower_is_better=False) for v in vals]
    assert pcts == sorted(pcts)
    assert ranks == sorted(ranks, reverse=True)
