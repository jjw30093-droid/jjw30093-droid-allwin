"""主客场分离的联赛百分位基准(PREMATCH_MOBILE_DATA_VISUALIZATION_V2 Phase 1.5)。

**状态(验收返工六如实标注)**:本模块公开的 `league_percentile()`(球队级
主客场百分位,如"主场进攻高于联赛 72% 的球队")**DEFERRED / 未接入产品**
——四张核心图目前都不展示这类百分位措辞。但模块内部的 `_league_team_ids`/
`_lookback_floor`/`_COMPATIBLE_TIERS`(mixed 档位排除规则)已经是
`backend/queries/matchup.py::league_situation_baseline()` 的真实依赖,
后者驱动图4"本场攻防对位"的关键对位判定——**这部分基础设施已经在生产
路径上**,只是 `league_percentile()` 这个面向球队整体的百分位函数本身
还没有消费者。

不能拿主队主场样本和客队客场样本共用同一个原始联赛均值——主客场对同一
指标(创造 xG、射门、禁区触球等)系统性有 17~18% 差异(见
`backend/queries/window.py` 文档),混在一起当同一参考系会让"高于联赛均值"
这句话在主客不同场景下失真。本模块因此为主场景、客场景**各自**建一套联赛
分布,只回答"这个值在同场景的联赛分布里排第几百分位",不做"好/坏"判断
——原始值的方向语义(越高越好还是越低越好)由调用方结合
`backend/metrics/registry.py` 的 `direction` 字段解释,这里只是百分位本身。

前台措辞只能用百分位("主场进攻高于联赛 72% 的球队"),**不得暴露
z-score、相关系数、置信区间**——这条红线来自方案 §二.2.3。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .window import DEFAULT_MAX_N, DEFAULT_MIN_N, _lookback_floor, venue_window

MIN_LEAGUE_SAMPLE = 5


@dataclass(frozen=True)
class PercentileResult:
    team_id: int
    is_home: bool
    value: float | None
    percentile: int | None
    league_sample_size: int

    @property
    def label_zh(self) -> str:
        venue = "主场" if self.is_home else "客场"
        if self.percentile is None:
            return f"{venue}数据不足,暂无法给出联赛百分位"
        return f"{venue}数值高于联赛 {self.percentile}% 的球队(样本 {self.league_sample_size} 队)"


def _league_team_ids(
    conn: sqlite3.Connection, league_id: int, before_boundary: str, lookback_floor: str, *, is_home: bool,
) -> list[int]:
    col = "Home_Team_ID" if is_home else "Away_Team_ID"
    rows = conn.execute(
        f"""SELECT DISTINCT {col} tid FROM dim_match
             WHERE League_ID=? AND status IN ('Finish','Finished')
               AND COALESCE(kickoff_at_utc, Date) < ? AND COALESCE(kickoff_at_utc, Date) >= ?""",
        (league_id, before_boundary, lookback_floor),
    ).fetchall()
    return [r["tid"] for r in rows]


_COMPATIBLE_TIERS = ("venue_full", "venue_partial")


def _team_scenario_mean(
    conn: sqlite3.Connection, team_id: int, league_id: int, before_boundary: str, field: str,
    *, is_home: bool, max_n: int, min_n: int,
) -> float | None:
    """该队在其对应主/客场景窗口(复用 `venue_window()` 选窗口,PIT-safe)内的
    该字段均值——**只有 tier 是 `venue_full`/`venue_partial`(该队自己真的有
    足够同场景历史)才计入分布**。`mixed` 档位是该队自己样本不足时合并主客场
    算出来的均值,口径已经和"纯同场景均值"不一样(验收返工三:此前这里
    的注释错误地把 mixed 也当同一口径接受,会用不可比的数字污染分布——
    见 `tests/backend/test_venue_baseline.py::TestMixedTierExcludedFromDistribution`
    的真实复现),`unavailable` 更是完全没有数据,两者都必须返回 None
    (该队不进入分布,不是该队的值算作 0 或缺省值)。"""
    w = venue_window(conn, team_id, league_id, before_boundary, is_home=is_home, max_n=max_n, min_n=min_n)
    if w.tier not in _COMPATIBLE_TIERS or not w.match_ids:
        return None
    placeholders = ",".join("?" for _ in w.match_ids)
    rows = conn.execute(
        f"""SELECT json_extract(extra_json, '$.' || ?) v FROM fact_team_match_stats
             WHERE Team_ID=? AND Period='All' AND Match_ID IN ({placeholders})""",
        [field, team_id, *w.match_ids],
    ).fetchall()
    vals = [r["v"] for r in rows if r["v"] is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def league_percentile(
    conn: sqlite3.Connection, team_id: int, league_id: int, before_boundary: str, field: str,
    *, is_home: bool, max_n: int = DEFAULT_MAX_N, min_n: int = DEFAULT_MIN_N,
) -> PercentileResult:
    """`team_id` 的该字段值,在同一场景(全部主队或全部客队,PIT-safe,取自
    `before_boundary` 之前)联赛分布里的百分位。

    百分位口径:分布里严格小于自身值的球队占比(不含自身),四舍五入到整数。
    联赛分布球队数(排除自身)不足 `MIN_LEAGUE_SAMPLE` 时返回 `percentile=None`
    ——不编造一个基于极小样本的百分位数字。
    """
    floor_ = _lookback_floor(before_boundary)
    team_ids = _league_team_ids(conn, league_id, before_boundary, floor_, is_home=is_home)
    distribution: dict[int, float] = {}
    for tid in team_ids:
        v = _team_scenario_mean(conn, tid, league_id, before_boundary, field, is_home=is_home, max_n=max_n, min_n=min_n)
        if v is not None:
            distribution[tid] = v

    own_value = distribution.get(team_id)
    others = [v for tid, v in distribution.items() if tid != team_id]
    if own_value is None or len(others) < MIN_LEAGUE_SAMPLE:
        return PercentileResult(
            team_id=team_id, is_home=is_home, value=own_value,
            percentile=None, league_sample_size=len(others),
        )

    below = sum(1 for v in others if v < own_value)
    percentile = round(100 * below / len(others))
    return PercentileResult(
        team_id=team_id, is_home=is_home, value=round(own_value, 4),
        percentile=max(0, min(100, percentile)), league_sample_size=len(others),
    )
