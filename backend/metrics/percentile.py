"""纯计算:把某队的值放进联赛分布(该场景下其它球队的值)里算百分位。

百分位口径与 `backend/queries/venue_baseline.py::league_percentile()` 完全
一致(该函数已实测、已测试、"前台只能用百分位,不得暴露 z-score/相关系数/
置信区间"这条红线正是从那里来的)——这里不重新发明一套算法,只是把它抽成
一个不碰数据库的纯函数,方便 `backend/queries/league_percentile.py` 的批量
查询在拿到分布数组后直接调用:

    分布里严格小于自身值的球队占比(不含自身),四舍五入取整;
    分布(不含自身)不足 MIN_LEAGUE_SAMPLE(5)时返回 None,不编造小样本百分位。

`direction="lower_better"` 的指标(被射门/xGA 等):不改写原始值本身,而是在
算百分位前把分布和自身值一起取负号——这样"数值小"的球队自然落在高百分位,
调用方拿到的 `percentile` 已经是统一语义的"越高越强/越好",不需要再自己
判断 direction 做二次翻转。`direction="style_only"` 的指标(控球率等)按
"越高百分位越高"处理,但前端措辞层必须遵守 `backend/metrics/registry.py`
的 `semantic="style"` 规则——不能写"更强",只能写"偏向/更多"。
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_LEAGUE_SAMPLE = 5


@dataclass(frozen=True)
class GroupPercentile:
    """一组指标(攻/守/控)的汇总百分位——只对该组里"两队都有百分位"的指标
    取平均,单个指标的 None 不参与汇总,也不当 0 拉低平均。"""

    value: float | None
    metrics_used: int


def percentile_of(
    own_value: float | None, others: list[float], *, lower_is_better: bool,
) -> int | None:
    """`own_value` 在 `others`(不含自身)里的百分位,方向已归一化为"越高越强"。

    `others` 里不应包含 `own_value` 本身——调用方负责在构建分布时把自身
    排除在外(同 `venue_baseline.py::league_percentile` 的既有约定)。
    """
    if own_value is None or len(others) < MIN_LEAGUE_SAMPLE:
        return None
    sign = -1.0 if lower_is_better else 1.0
    ov = sign * own_value
    below = sum(1 for v in others if sign * v < ov)
    pct = round(100 * below / len(others))
    return max(0, min(100, pct))


def group_percentile(percentiles: list[int | None], *, min_metrics: int = 2) -> GroupPercentile:
    """一组百分位的平均值——只在至少 `min_metrics` 个指标都给出了百分位时才
    汇总,否则整组诚实返回 None(不用 1 个指标的百分位冒充"整组表现")。"""
    present = [p for p in percentiles if p is not None]
    if len(present) < min_metrics:
        return GroupPercentile(value=None, metrics_used=len(present))
    return GroupPercentile(value=sum(present) / len(present), metrics_used=len(present))


@dataclass(frozen=True)
class Highlight:
    key: str
    name_zh: str
    home_percentile: int
    away_percentile: int
    gap: int
    home_value: float
    away_value: float


# Δ百分位 < 此值一律措辞为"基本持平",不进"最大差距"榜——分位数会把
# "差 1%"和"差 71%"都压到同一根轴上,不设门槛会让噪声级差距也显得"明显"。
GAP_FLOOR = 15


def top_gaps(rows: list[Highlight], *, k: int = 3, floor: int = GAP_FLOOR) -> list[Highlight]:
    """按 |Δ百分位| 降序取前 k 条,门槛以下的一律不进榜。"""
    qualified = [r for r in rows if r.gap >= floor]
    return sorted(qualified, key=lambda r: r.gap, reverse=True)[:k]


def gap_wording(gap: int) -> str:
    if gap >= 40:
        return "明显更强"
    if gap >= 25:
        return "更强一些"
    if gap >= 15:
        return "略占优势"
    return "基本持平"


@dataclass(frozen=True)
class PeerCandidate:
    team_id: int
    percentile: float


def nearest_peers(
    target_id: int, group_percentiles: dict[int, float], *, k: int = 2,
) -> list[PeerCandidate]:
    """`target_id` 在 `group_percentiles`(同场景全联赛的组级百分位)里最接近
    的 `k` 支球队——按 |Δ百分位| 升序,并列按 team_id 定序(避免结果在两次
    请求之间抖动)。

    `group_percentiles` 应该是**同一个 venue 分布**下算出的组级百分位
    (全联赛每队一个值,不只是本场两队)——不能拿主场分布的百分位去比对
    客场分布的球队,那样"接近"这件事本身就不成立(见 `league_percentile.py`
    §二的主客场分离设计)。

    目标队自己不在 `group_percentiles` 里(样本不足等)时返回空列表,不编造
    "最接近"的答案。
    """
    target_pct = group_percentiles.get(target_id)
    if target_pct is None:
        return []
    candidates = [(tid, pct) for tid, pct in group_percentiles.items() if tid != target_id]
    candidates.sort(key=lambda t: (abs(t[1] - target_pct), t[0]))
    return [PeerCandidate(team_id=tid, percentile=pct) for tid, pct in candidates[:k]]
