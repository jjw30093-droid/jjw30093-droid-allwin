"""联赛百分位画像(数据 tab「风格」子 tab 重构:批量版 `venue_baseline.py`)。

**为什么不能逐队调用 `venue_window()` 再各自查一次**:`venue_baseline.py::
league_percentile()` 对每个 (球队, 字段) 都要跑一次 `venue_window()` + 一次
stats 查询——对一个 ~20 队的联赛,单个字段的分布就是 20 次独立查询,乘上
本模块要展示的 10 个字段会是 200 次。本模块用一条按 (Team_ID, venue) 分区
的 `ROW_NUMBER()` 窗口函数一次性选出"每队自己的近 max_n 场同主客场比赛"、
一次性聚合多个字段,复刻 `window.py::_match_ids()` 完全相同的
WHERE/ORDER BY/LIMIT 语义——`tests/backend/test_league_percentile.py::
test_batched_window_matches_venue_window` 逐队断言两条路径选出的
match_id 集合完全相同,防止选窗口逻辑出现第二份实现后悄悄漂移。

**故意复用 `venue_window()` 的选口径**(`League_ID` + 730 天回溯,不像
`team_style_preview.py` 那样额外按 `Season` 收紧)——这样两队自己的百分位
分布球队池,与页面上其它仍在用 `venue_window()` 的模块(`attack_chain.py`/
`possession_control.py`/`defensive_pressure.py`/`matchup.py`)展示的原始
数值保持完全一致的窗口语义,不会出现"同一份 preview 响应里,风格模块选了
另一批历史比赛"这种静默分叉。**已知风险**(继承自 `venue_baseline.py`/
`matchup.py` 现有代码,不是本模块新引入的问题):730 天回溯不是按赛季边界
收紧,理论上赛季刚开始时仍可能把上上赛季末尾、已经不在本联赛的球队囊括
进候选(`dim_match.League_ID` 跨赛季持久)。这与 `team_style_preview.py`
2026-08-25 修复的事故是同一类风险,但那次事故的诱因是"完全没有时间下限",
这里至少有 730 天硬顶——这个硬顶在赛季边界附近是否足够,不在本轮工作范围
内验证,留给独立复核。

**百分位资格 = 严格复刻 `venue_baseline.py::_COMPATIBLE_TIERS` 的哲学**:
只有同一 venue 下真的凑够 `min_n`(默认 5)场的球队才进入分布、才有自己的
百分位——不像 `venue_window()` 那样在同场景样本不足时退回"合并主客场"的
`mixed` 档位(那个档位的口径已经和"纯同场景均值"不一样,`venue_baseline.py`
的真实复现证明混进分布会污染其它球队的百分位)。**这是相对于
`attack_chain.py` 等模块的行为收窄**:本模块的每支球队(含本场两队)如果
本赛季同 venue 场次 < min_n,该 venue 下的全部指标一律"暂无法给出百分位",
不做 `mixed` 兜底——赛季初可能出现整块空白,这是诚实降级,不是 bug。

前台措辞只能用百分位,不得暴露具体排名以外的统计量(z-score/相关系数/
置信区间)——同 `venue_baseline.py` 的既有红线。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Literal

from backend.media.team_crests import resolve_team_crest_url
from backend.metrics.percentile import (
    GAP_FLOOR,
    Highlight,
    PeerCandidate,
    gap_wording,
    group_percentile,
    nearest_peers,
    percentile_of,
    top_gaps,
)
from backend.metrics.registry import Direction, MetricDef, get_metric
from backend.queries.teams import display_name_for_team, team_display_for
from backend.queries.window import DEFAULT_MAX_N, DEFAULT_MIN_N, _lookback_floor

# canonical_key -> extra_json 字段名(自身球队那一行)
_OWN_AVG_FIELDS: dict[str, str] = {
    "xg": "expected_goals",
    "shots": "total_shots",
    "shots_on_target": "ShotsOnTarget",
    "touches_opp_box": "touches_opp_box",
    "xgot": "expected_goals_on_target",
    "possession": "BallPossesion",
}
# canonical_key -> extra_json 字段名(同场对手那一行——被射门/让出xG 一类)
_OPPONENT_AVG_FIELDS: dict[str, str] = {
    "shots_faced": "total_shots",
    "shots_on_target_faced": "ShotsOnTarget",
    "xga": "expected_goals",
    "box_shots_faced": "shots_inside_box",
}
# canonical_key -> (分子字段, 分母字段),同一场比赛内配对相除后再对窗口求和
_RATIO_FIELDS: dict[str, tuple[str, str]] = {
    "pass_completion": ("accurate_passes", "passes"),
    "opp_half_pass_share": ("opposition_half_passes", "passes"),
}

# 查询层的三档场地口径,取值与 `window.py::_match_ids` 的 `venue` 参数同名同义
# (那里是 WHERE 子句、要绑两次 team_id;这里是 JOIN 谓词、比较两个表的列,
# 不引入任何占位符——见 `_venue_join()`)。
Venue = Literal["home", "away", "any"]

# 产品层的两档口径,与查询层的 `Venue` **刻意分开**:"same_venue" 会展开成
# home/away 两次分布查询,"all" 展开成一次 `any`——一个产品选项对应几次查询
# 是实现细节,不该泄漏进前端能看到的枚举里。
VenueMode = Literal["same_venue", "all"]

GROUPS: dict[str, list[str]] = {
    "attack": ["xg", "shots", "shots_on_target", "touches_opp_box", "xgot"],
    "defence": ["xga", "shots_faced", "shots_on_target_faced", "box_shots_faced"],
    "control": ["possession", "pass_completion", "opp_half_pass_share"],
}
GROUP_TITLE_ZH = {"attack": "攻", "defence": "守", "control": "控"}


@dataclass(frozen=True)
class TeamMetricValue:
    value: float | None
    matches_with_data: int
    window_matches: int  # 该 venue 下这支队一共取到的场次(所有字段共享同一个窗口)

    @property
    def complete(self) -> bool:
        return self.window_matches > 0 and self.matches_with_data == self.window_matches


def _venue_join(venue: Venue) -> str:
    """`fact_team_match_stats` ↔ `dim_match` 的场地 JOIN 谓词(整条,不只是列名)。

    内部固定字符串,不接收外部输入——同 team_style_preview.py 的既有写法。

    **"any" 分支不引入任何新占位符**,因为它比较的是两个表的列而不是绑定值
    (对比 `window.py::_match_ids` 的 `venue="any"`,那里是 `(m.Home_Team_ID=?
    OR m.Away_Team_ID=?)`,要绑两次 team_id)。这正是三档场地口径能零风险接进
    来的原因:`_ranked_params()` 的绑定顺序一个字都不用动。改这个函数时必须
    守住这条性质。
    """
    return {
        "home": "t.Team_ID=m.Home_Team_ID",
        "away": "t.Team_ID=m.Away_Team_ID",
        "any": "(t.Team_ID=m.Home_Team_ID OR t.Team_ID=m.Away_Team_ID)",
    }[venue]


def _ranked_cte(
    venue: Venue, *, with_opponent: bool, league_scoped: bool = True, team_count: int = 0,
) -> str:
    """`league_scoped=True`、`team_count=0` 且 `venue in ("home","away")` 时拼出的
    SQL 与放宽之前**逐字节相同**——常规联赛路径零回归的落点。

    `team_count>0` 把候选收窄到指定球队。跨赛事模式(`league_scoped=False`)
    **必须**用它:没有联赛谓词时这条 CTE 会扫全库每一支球队的历史,而那时
    我们只需要本场两队的数值(不算分布,见 `cross_league_metric_values`)。
    """
    opp_col = ", CASE WHEN t.Team_ID=m.Home_Team_ID THEN m.Away_Team_ID ELSE m.Home_Team_ID END opp_id" if with_opponent else ""
    league_clause = "m.League_ID=? AND " if league_scoped else ""
    team_clause = (
        f"\n         AND t.Team_ID IN ({','.join('?' * team_count)})" if team_count else ""
    )
    return f"""
      SELECT t.Team_ID tid, t.Match_ID mid, t.extra_json extra_json{opp_col},
             ROW_NUMBER() OVER (
               PARTITION BY t.Team_ID
               ORDER BY COALESCE(m.kickoff_at_utc, m.Date) DESC, m.Match_ID DESC
             ) rn
        FROM dim_match m
        JOIN fact_team_match_stats t ON t.Match_ID=m.Match_ID AND t.Period='All'
                                     AND {_venue_join(venue)}
       WHERE {league_clause}m.status IN ('Finish','Finished')
         AND COALESCE(m.kickoff_at_utc, m.Date) < ?
         AND COALESCE(m.kickoff_at_utc, m.Date) >= ?{team_clause}
    """


def _ranked_params(
    league_id: int | None, before_boundary: str, floor_: str,
    team_ids: tuple[int, ...] | None, max_n: int,
) -> list[Any]:
    """`_ranked_cte()` 的绑定值,顺序必须与它拼出的占位符一一对应。"""
    params: list[Any] = [] if league_id is None else [league_id]
    params += [before_boundary, floor_]
    if team_ids:
        params += list(team_ids)
    params.append(max_n)
    return params


def league_window_matches(
    conn: sqlite3.Connection, league_id: int | None, before_boundary: str, *, venue: Venue,
    max_n: int = DEFAULT_MAX_N, team_ids: tuple[int, ...] | None = None,
) -> dict[int, list[int]]:
    """每支球队近 max_n 场同 venue 比赛的 match_id 列表(全联赛一次查完)。

    仅用于测试断言与 `_league_own_avg_batch` 之类函数共享同一份选窗口逻辑
    ——真正的字段聚合走各自的批量函数,不逐队复用这份 match_id 再单独查询
    (那样又变回 N+1)。
    """
    floor_ = _lookback_floor(before_boundary)
    sql = f"""
      WITH ranked AS ({_ranked_cte(venue, with_opponent=False, league_scoped=league_id is not None, team_count=len(team_ids or ()))})
      SELECT tid, mid FROM ranked WHERE rn<=?
      ORDER BY tid, mid
    """
    rows = conn.execute(sql, _ranked_params(league_id, before_boundary, floor_, team_ids, max_n)).fetchall()
    out: dict[int, list[int]] = {}
    for r in rows:
        out.setdefault(int(r["tid"]), []).append(int(r["mid"]))
    return out


def _league_own_avg_batch(
    conn: sqlite3.Connection, league_id: int | None, before_boundary: str, floor_: str, *,
    venue: Venue, max_n: int, fields: dict[str, str], team_ids: tuple[int, ...] | None = None,
) -> dict[int, dict[str, TeamMetricValue]]:
    if not fields:
        return {}
    # 字段名全部来自本文件顶部的固定字典(_OWN_AVG_FIELDS 等),不是外部输入,
    # 拼进 SQL 文本是安全的——同 team_style_preview.py 的既有写法。
    cols = ", ".join(
        f"AVG(json_extract(extra_json,'$.{f}')) v_{k}, COUNT(json_extract(extra_json,'$.{f}')) k_{k}"
        for k, f in fields.items()
    )
    sql = f"""
      WITH ranked AS ({_ranked_cte(venue, with_opponent=False, league_scoped=league_id is not None, team_count=len(team_ids or ()))}),
      last_n AS (SELECT tid, extra_json FROM ranked WHERE rn<=?)
      SELECT tid, COUNT(*) n, {cols} FROM last_n GROUP BY tid
    """
    rows = conn.execute(sql, _ranked_params(league_id, before_boundary, floor_, team_ids, max_n)).fetchall()
    out: dict[int, dict[str, TeamMetricValue]] = {}
    for r in rows:
        n = r["n"]
        out[int(r["tid"])] = {
            k: TeamMetricValue(value=r[f"v_{k}"], matches_with_data=r[f"k_{k}"] or 0, window_matches=n)
            for k in fields
        }
    return out


def _league_opponent_avg_batch(
    conn: sqlite3.Connection, league_id: int | None, before_boundary: str, floor_: str, *,
    venue: Venue, max_n: int, fields: dict[str, str], team_ids: tuple[int, ...] | None = None,
) -> dict[int, dict[str, TeamMetricValue]]:
    if not fields:
        return {}
    cols = ", ".join(
        f"AVG(json_extract(opp.extra_json,'$.{f}')) v_{k}, COUNT(json_extract(opp.extra_json,'$.{f}')) k_{k}"
        for k, f in fields.items()
    )
    sql = f"""
      WITH ranked AS ({_ranked_cte(venue, with_opponent=True, league_scoped=league_id is not None, team_count=len(team_ids or ()))}),
      last_n AS (SELECT tid, mid, opp_id FROM ranked WHERE rn<=?)
      SELECT l.tid tid, COUNT(*) n, {cols}
        FROM last_n l
        JOIN fact_team_match_stats opp ON opp.Match_ID=l.mid AND opp.Team_ID=l.opp_id AND opp.Period='All'
       GROUP BY l.tid
    """
    rows = conn.execute(sql, _ranked_params(league_id, before_boundary, floor_, team_ids, max_n)).fetchall()
    out: dict[int, dict[str, TeamMetricValue]] = {}
    for r in rows:
        n = r["n"]
        out[int(r["tid"])] = {
            k: TeamMetricValue(value=r[f"v_{k}"], matches_with_data=r[f"k_{k}"] or 0, window_matches=n)
            for k in fields
        }
    return out


def _league_ratio_batch(
    conn: sqlite3.Connection, league_id: int | None, before_boundary: str, floor_: str, *,
    venue: Venue, max_n: int, fields: dict[str, tuple[str, str]], scale: float = 100.0,
    team_ids: tuple[int, ...] | None = None,
) -> dict[int, dict[str, TeamMetricValue]]:
    if not fields:
        return {}
    parts = []
    for k, (num, den) in fields.items():
        cond = (
            f"json_extract(extra_json,'$.{num}') IS NOT NULL AND "
            f"json_extract(extra_json,'$.{den}') IS NOT NULL AND json_extract(extra_json,'$.{den}')>0"
        )
        parts.append(
            f"SUM(CASE WHEN {cond} THEN json_extract(extra_json,'$.{num}') ELSE 0 END) numsum_{k}, "
            f"SUM(CASE WHEN {cond} THEN json_extract(extra_json,'$.{den}') ELSE 0 END) densum_{k}, "
            f"SUM(CASE WHEN {cond} THEN 1 ELSE 0 END) k_{k}"
        )
    cols = ", ".join(parts)
    sql = f"""
      WITH ranked AS ({_ranked_cte(venue, with_opponent=False, league_scoped=league_id is not None, team_count=len(team_ids or ()))}),
      last_n AS (SELECT tid, extra_json FROM ranked WHERE rn<=?)
      SELECT tid, COUNT(*) n, {cols} FROM last_n GROUP BY tid
    """
    rows = conn.execute(sql, _ranked_params(league_id, before_boundary, floor_, team_ids, max_n)).fetchall()
    out: dict[int, dict[str, TeamMetricValue]] = {}
    for r in rows:
        n = r["n"]
        m: dict[str, TeamMetricValue] = {}
        for k in fields:
            ksum = r[f"k_{k}"] or 0
            densum = r[f"densum_{k}"]
            value = scale * r[f"numsum_{k}"] / densum if ksum > 0 and densum else None
            m[k] = TeamMetricValue(value=value, matches_with_data=ksum, window_matches=n)
        out[int(r["tid"])] = m
    return out


def league_metric_distribution(
    conn: sqlite3.Connection, league_id: int, before_boundary: str, *,
    venue: Venue, max_n: int = DEFAULT_MAX_N, min_n: int = DEFAULT_MIN_N,
) -> dict[int, dict[str, TeamMetricValue]]:
    """全联赛(该 venue)每支球队的 12 项指标——6 条批量 SQL(own-avg / opponent-avg
    / ratio,各一条),不是逐队逐字段查询。样本不足 `min_n` 的球队从返回结果里
    整体剔除(不产出该队任何字段,不是产出 `value=None` 的空壳)——这样调用方
    在构建"其它球队分布"时只需过滤 `team_id != 自身`,不需要再检查 tier。
    """
    floor_ = _lookback_floor(before_boundary)
    own = _league_own_avg_batch(conn, league_id, before_boundary, floor_, venue=venue, max_n=max_n, fields=_OWN_AVG_FIELDS)
    opp = _league_opponent_avg_batch(conn, league_id, before_boundary, floor_, venue=venue, max_n=max_n, fields=_OPPONENT_AVG_FIELDS)
    ratio = _league_ratio_batch(conn, league_id, before_boundary, floor_, venue=venue, max_n=max_n, fields=_RATIO_FIELDS)

    merged: dict[int, dict[str, TeamMetricValue]] = {}
    for tid, metrics in own.items():
        if metrics and next(iter(metrics.values())).window_matches >= min_n:
            merged.setdefault(tid, {}).update(metrics)
    for tid, metrics in opp.items():
        if metrics and next(iter(metrics.values())).window_matches >= min_n:
            merged.setdefault(tid, {}).update(metrics)
    for tid, metrics in ratio.items():
        if metrics and next(iter(metrics.values())).window_matches >= min_n:
            merged.setdefault(tid, {}).update(metrics)
    # 三条批量 SQL 各自基于同一个 (league_id, venue, max_n) 选窗口,window_matches
    # 理应一致;样本不足的球队三条 SQL 都会给同样小的 n,一致被剔除。
    return merged


def cross_league_metric_values(
    conn: sqlite3.Connection, before_boundary: str, *,
    venue: Venue, team_ids: tuple[int, ...], max_n: int = DEFAULT_MAX_N,
) -> dict[int, dict[str, TeamMetricValue]]:
    """指定球队近 max_n 场同 venue 比赛的指标值——**不限赛事**。

    给欧战这类跨联赛赛事用(见 `queries/leagues.py::is_cross_league_competition`)。
    与 `league_metric_distribution()` 的两处**故意**不同:

    1. **不圈联赛**。欧冠联赛阶段每队只踢 8 场(主客各 4),圈在本赛事内同主
       客场样本永远到不了 min_n,整块恒空(生产实测欧冠够格球队 0 支)。
    2. **不做 min_n 过滤,也不构造分布**。这条路径的产出只用于"并排列出两队
       各自的原始数值",不算百分位——两队分处不同联赛,没有共同的参照人群,
       硬凑一个出来是编造。因此"够不够 5 场"这个百分位资格门槛在这里没有
       意义,有几场就如实用几场,场次由 `window_matches` 如实带出。

    必须传 `team_ids`:没有联赛谓词时不收窄会扫全库每一支球队的历史。
    """
    if not team_ids:
        return {}
    floor_ = _lookback_floor(before_boundary)
    merged: dict[int, dict[str, TeamMetricValue]] = {}
    for batch in (
        _league_own_avg_batch(conn, None, before_boundary, floor_, venue=venue,
                              max_n=max_n, fields=_OWN_AVG_FIELDS, team_ids=team_ids),
        _league_opponent_avg_batch(conn, None, before_boundary, floor_, venue=venue,
                                   max_n=max_n, fields=_OPPONENT_AVG_FIELDS, team_ids=team_ids),
        _league_ratio_batch(conn, None, before_boundary, floor_, venue=venue,
                            max_n=max_n, fields=_RATIO_FIELDS, team_ids=team_ids),
    ):
        for tid, metrics in batch.items():
            merged.setdefault(tid, {}).update(metrics)
    return merged


@dataclass(frozen=True)
class MetricProfileDTO:
    key: str
    name_zh: str
    unit: str
    direction: Direction
    # 前端措辞红线来自这个字段,不是 direction:"style"(如控球率)只能写
    # "偏向/较多",不能写"更强"——即使 direction="style_only" 也一样。
    # 与 direction 目前对本组 12 个指标恰好一一对应,但语义上是两件事,
    # 显式传递避免以后新增 outcome_variance 类指标时被隐式假设绊倒。
    semantic: str
    home_value: float | None
    away_value: float | None
    home_percentile: int | None
    away_percentile: int | None
    home_complete: bool
    away_complete: bool
    league_sample_size: int


@dataclass(frozen=True)
class PeerTeamDTO:
    team_id: int
    name: str
    crest_url: str | None
    percentile: float


@dataclass(frozen=True)
class GroupProfileDTO:
    key: str
    title_zh: str
    metrics: list[MetricProfileDTO]
    home_group_percentile: float | None
    away_group_percentile: float | None
    # 2026-09 第二轮:站长要的不只是"分不清哪个点是哪队",而是"利物浦
    # 类似什么水平、布伦特福德类似什么水平"——同场景联赛分布里组级百分位
    # 最接近的 2 支球队。主队对**主场分布**找、客队对**客场分布**找,
    # 与本组百分位本身用的是同一套分布,不跨场景混着找"相似"。
    home_peers: list[PeerTeamDTO]
    away_peers: list[PeerTeamDTO]


@dataclass(frozen=True)
class MatchDataProfileDTO:
    home_matches: int
    away_matches: int
    home_available: bool
    away_available: bool
    groups: list[GroupProfileDTO]
    highlights: list[Highlight]
    unavailable_reason: str | None = None
    # "league_percentile" = 常规:两队各自对本联赛同场景分布取百分位。
    # "cross_league_raw" = 欧战等跨联赛赛事:没有共同参照人群,只并排列出
    # 两队各自的原始数值,全部 percentile 为 None。
    # 前端据这个字段分支,**不靠"percentile 全是 null"嗅探**——那种嗅探
    # 在"联赛模式下恰好所有指标都缺样本"时会误判成杯赛模式。
    comparison_mode: str = "league_percentile"
    # 杯赛模式下为什么没有百分位、这些数字到底是什么口径。由后端出文案,
    # 保证与实际取数逻辑同源,不靠前端另写一句可能漂移的解释。
    scope_note: str | None = None
    # ↓ 2026-09-13 站长要的两个切换器:「全部 / 相同主客场」×「近 3/5/10 场」。
    # 这两个字段回答"你现在看到的这份画像是按什么口径取出来的"。**放在 DTO
    # 内部而不是响应外层**:同一个对象会经两条完全不同的路径到达同一批组件
    # ——① /preview 内嵌(RSC 直出,外层是整个 preview,没有专属 wrapper 可读);
    # ② /matches/{id}/data-profile(有 wrapper)。放外层的话 RSC 路径读不到,
    # 组件只能各自假设一个默认值,那正是"文案与实际取数逻辑漂移"的经典入口
    # (同上面 comparison_mode/scope_note 放进 DTO 的同一条理由)。
    #
    # venue_mode 与 comparison_mode 是**正交**的两个维度,不合并成一个枚举:
    # 前者答"分布怎么圈(分不分主客场)",后者答"有没有共同参照人群(联赛内/
    # 跨赛事)"。合并会让 cross_league_raw + all 这种真实存在的组合无法表达。
    venue_mode: str = "same_venue"
    # 向后要了几场(窗口长度上限)。实际用到几场看 home_matches/away_matches,
    # 可能更少——这个字段只说请求口径,不说实际样本。
    window_n: int = DEFAULT_MAX_N


def _team_window_matches(dist: dict[int, dict[str, TeamMetricValue]], team_id: int) -> int:
    row = dist.get(team_id)
    if not row:
        return 0
    return next(iter(row.values())).window_matches


def _all_team_group_percentiles(
    dist: dict[int, dict[str, TeamMetricValue]], keys: list[str],
) -> dict[int, float]:
    """`dist` 里**每一支**球队(不只是本场两队)在这一组指标上的组级百分位。

    对标球队功能需要"全联赛谁跟目标队接近",不是只算目标队自己——
    `match_data_profile()` 原来在算目标队百分位时把其余球队的 `tid` 丢掉了
    (只留裸浮点数分布),这里换一种遍历顺序:对分布里的每支球队各自算一遍
    "以它为自身、其余队为分布"的组级百分位,O(n²×指标数),n≈20 时可忽略。
    只返回组级百分位不是 None 的球队(至少 2 个指标都有百分位)。
    """
    out: dict[int, float] = {}
    for tid, row in dist.items():
        pcts: list[int | None] = []
        for key in keys:
            meta = get_metric(key)
            lower_is_better = meta.direction == "lower_better"
            own_value = row.get(key).value if key in row else None
            others = [
                other_row[key].value for other_tid, other_row in dist.items()
                if other_tid != tid and key in other_row and other_row[key].value is not None
            ]
            pcts.append(percentile_of(own_value, others, lower_is_better=lower_is_better))
        gp = group_percentile(pcts)
        if gp.value is not None:
            out[tid] = gp.value
    return out


def _resolve_peers(
    candidates: list[PeerCandidate],
    display: dict[int, Any],
    crest_map: dict[int, str | None],
) -> list[PeerTeamDTO]:
    """把 `nearest_peers()` 的裸 (team_id, percentile) 结果补上中文名和队徽。

    `display`/`crest_map` 必须是调用方对**全部分组、两侧候选的 team_id 并集**
    一次性查出来的(见 `team_style_preview.py:194-199` 的 2026-08-19 性能
    修复注释:先收并集再一次性查,不在循环里逐队重复调用
    `team_display_for`/`resolve_team_crest_url`——后者会读本地文件)。
    """
    return [
        PeerTeamDTO(
            team_id=c.team_id,
            name=display_name_for_team(c.team_id, display=display),
            crest_url=crest_map.get(c.team_id),
            percentile=c.percentile,
        )
        for c in candidates
    ]


# 2026-09-10 站长复看:原文案把"为什么没有百分位"的完整推导(欧冠每队只踢
# 8 场、凑不出分布…)全写在手机首屏上,一百多字。站长原话"手机端说明的文字
# 都太多了,只要说一下数据来自最近比赛,即可"。推导过程属于工程理由,不是
# 用户要读的东西;这里只留两件用户真正需要知道的事:数字是哪来的、能不能
# 拿来比强弱。
CROSS_LEAGUE_SCOPE_NOTE = "数据取自两队各自最近的比赛,不限赛事;两队不在同一联赛,不比强弱。"

# 「全部」口径(venue_mode="all")的口径说明。同一条"手机端别写长文"的纪律:
# 只说用户真正需要知道的两件事——得到了什么(同一把尺子)、付出了什么(主客场
# 差异被抹平)。只说前者是营销,只说后者是劝退。
# "same_venue" 不给 scope_note(保持今天的零视觉回归)。
ALL_VENUE_SCOPE_NOTE = "不分主客场,两队落在同一套联赛分布上;代价是主客场差异被抹平在均值里。"

# 「两侧都不够格」时的文案必须跟着口径走:用户明明选了"不分主客场",页面却说
# "同主客场比赛不足",那是错误陈述(同 window.py::label_zh 的同一条纪律)。
_UNAVAILABLE_BY_VENUE: dict[str, str] = {
    "same_venue": "两队本赛季同主客场比赛都不足,暂无法给出联赛百分位画像。",
    "all": "两队本赛季比赛场次都不足,暂无法给出联赛百分位画像。",
}


def _scope_note_for(venue_mode: str, *, cross_league: bool) -> str | None:
    """两个维度各自贡献一句,拼接而不是二选一——跨联赛比赛同样可以切「全部」,
    那时"不限赛事"和"不分主客场"两件事都成立,漏掉任何一句都是隐瞒。"""
    parts = [CROSS_LEAGUE_SCOPE_NOTE] if cross_league else []
    if venue_mode == "all":
        parts.append(ALL_VENUE_SCOPE_NOTE)
    return " ".join(parts) if parts else None


def _cross_league_profile(
    conn: sqlite3.Connection, before_boundary: str, home_id: int, away_id: int, *, max_n: int,
    venue_mode: VenueMode = "same_venue",
) -> MatchDataProfileDTO:
    """跨联赛赛事的原始数值画像——三组照填,但全部 percentile 为 None。

    `groups` 必须照填三段而不是返回空列表:前端是 `groups.map()` 渲染的,
    给空列表等于攻/守/控三块**整块消失且页面上零解释**(2026-09-10 站长
    报告的正是这个现象)。

    **两个切换器在这条路径上同样生效**(2026-09-13):它们改变的是"取哪些
    历史比赛",这件事在跨联赛模式下一样成立、一样改变屏幕上的数字。不支持
    就得让前端在欧战比赛上把切换器藏起来——"点进欧冠切换器凭空消失"比多这
    几行糟得多。跨联赛与分不分主客场是正交的两个维度,见 DTO 里的注释。
    """
    venue_home: Venue = "any" if venue_mode == "all" else "home"
    venue_away: Venue = "any" if venue_mode == "all" else "away"
    home_vals = cross_league_metric_values(conn, before_boundary, venue=venue_home, team_ids=(home_id,), max_n=max_n)
    away_vals = cross_league_metric_values(conn, before_boundary, venue=venue_away, team_ids=(away_id,), max_n=max_n)

    home_matches = _team_window_matches(home_vals, home_id)
    away_matches = _team_window_matches(away_vals, away_id)
    scope_note = _scope_note_for(venue_mode, cross_league=True)

    if home_matches == 0 and away_matches == 0:
        return MatchDataProfileDTO(
            home_matches=0, away_matches=0, home_available=False, away_available=False,
            groups=[], highlights=[],
            unavailable_reason="两队近期都没有可用的比赛数据。",
            comparison_mode="cross_league_raw", scope_note=scope_note,
            venue_mode=venue_mode, window_n=max_n,
        )

    groups: list[GroupProfileDTO] = []
    for group_key, keys in GROUPS.items():
        metrics: list[MetricProfileDTO] = []
        for key in keys:
            meta: MetricDef = get_metric(key)
            home_row = home_vals.get(home_id, {}).get(key)
            away_row = away_vals.get(away_id, {}).get(key)
            home_value = home_row.value if home_row else None
            away_value = away_row.value if away_row else None
            metrics.append(MetricProfileDTO(
                key=key, name_zh=meta.name_zh, unit=meta.unit, direction=meta.direction,
                semantic=meta.semantic,
                home_value=round(home_value, 3) if home_value is not None else None,
                away_value=round(away_value, 3) if away_value is not None else None,
                home_percentile=None, away_percentile=None,
                home_complete=home_row.complete if home_row else False,
                away_complete=away_row.complete if away_row else False,
                league_sample_size=0,  # 没有参照人群,不是"人群里 0 支队"而是这个概念不适用
            ))
        groups.append(GroupProfileDTO(
            key=group_key, title_zh=GROUP_TITLE_ZH[group_key], metrics=metrics,
            home_group_percentile=None, away_group_percentile=None,
            home_peers=[], away_peers=[],  # 对标球队同样需要分布,这里没有
        ))

    return MatchDataProfileDTO(
        home_matches=home_matches, away_matches=away_matches,
        home_available=home_matches > 0, away_available=away_matches > 0,
        groups=groups,
        highlights=[],  # 「最大的差距」榜是按百分位差排的,没有百分位就没有这个榜
        unavailable_reason=None,
        comparison_mode="cross_league_raw", scope_note=scope_note,
        venue_mode=venue_mode, window_n=max_n,
    )


def match_data_profile(
    conn: sqlite3.Connection, league_id: int | None, before_boundary: str, home_id: int, away_id: int,
    *, max_n: int = DEFAULT_MAX_N, min_n: int | None = None, cross_league: bool = False,
    venue_mode: VenueMode = "same_venue",
) -> MatchDataProfileDTO:
    """本场两队的攻/守/控三组联赛百分位画像。

    `venue_mode="same_venue"`(默认,今天的行为):主队对**联赛主场分布**取
    百分位,客队对**联赛客场分布**取百分位——两套独立分布,不共用同一个原始
    联赛均值(主客场系统性差异 17~18%,见 `window.py` 文档)。两队因此不在
    同一条绝对数值尺上,但在各自的百分位尺上可比,前端措辞必须讲清楚这一点。

    `venue_mode="all"`(2026-09-13 新增的切换器):不分主客场、**仍限本联赛**
    ——只算一次分布,两队落在**同一套**尺子上。这不是"不限赛事"(那是
    `cross_league`,正交的另一个维度)。

    `cross_league=True`(欧战三项)走另一条路:两队来自不同国内联赛,没有
    共同的参照人群,改为只列两队各自不限赛事的近期原始数值,不给百分位。
    """
    # `min_n` 必须跟着窗口长度走,不能写死。实测:max_n=3 配死的 min_n=5 →
    # 全联赛够格球队 0 支 → 两侧 available 双 False → 攻/守/控三块整块消失,
    # 而且 unavailable_reason 会把"后端门槛写死"说成"两队比赛不足",是错误
    # 陈述。N=10 → min(5,10)=5,与本参数引入之前逐字节等价,零回归。
    # 放在这里而不是 `league_metric_distribution()`:"窗口多短就把门槛降到
    # 多低"是产品口径决策,不是查询层的默认值;查询层继续接受显式 min_n,
    # 谁调谁负责。哨兵用 None 而不是 DEFAULT_MIN_N,这样调用方显式传 5
    # 与不传是两件可以区分的事(测试要能钉死"是联动在救它")。
    effective_min_n = min_n if min_n is not None else min(DEFAULT_MIN_N, max_n)

    if cross_league:
        return _cross_league_profile(
            conn, before_boundary, home_id, away_id, max_n=max_n, venue_mode=venue_mode,
        )
    if league_id is None:
        # 接线错误要大声失败:没有联赛就构造不出百分位分布,静默返回空画像
        # 会变成"页面莫名其妙没数据"——正是本次要修的那个故障形态。
        raise ValueError("league_id 为 None 时必须显式传 cross_league=True")

    if venue_mode == "all":
        # 不分主客场 = 两队落在**同一套**分布上,只需要查一次(不是查两次再
        # 各取各的)。下游 `_team_window_matches`/`percentile_of`/`nearest_peers`
        # 只吃 dist 字典,两侧指向同一个对象时语义正确:在同一把尺子上找邻居。
        home_dist = away_dist = league_metric_distribution(
            conn, league_id, before_boundary, venue="any", max_n=max_n, min_n=effective_min_n,
        )
    else:
        home_dist = league_metric_distribution(conn, league_id, before_boundary, venue="home", max_n=max_n, min_n=effective_min_n)
        away_dist = league_metric_distribution(conn, league_id, before_boundary, venue="away", max_n=max_n, min_n=effective_min_n)

    home_matches = _team_window_matches(home_dist, home_id)
    away_matches = _team_window_matches(away_dist, away_id)
    home_available = home_id in home_dist
    away_available = away_id in away_dist
    scope_note = _scope_note_for(venue_mode, cross_league=False)

    if not home_available and not away_available:
        return MatchDataProfileDTO(
            home_matches=home_matches, away_matches=away_matches,
            home_available=False, away_available=False, groups=[], highlights=[],
            unavailable_reason=_UNAVAILABLE_BY_VENUE[venue_mode],
            scope_note=scope_note, venue_mode=venue_mode, window_n=max_n,
        )

    # 两遍循环:第一遍算每组的指标百分位 + 全联赛组级百分位(用来找对标队),
    # 先把候选(裸 team_id,还没查名字/队徽)收集起来;等全部分组、两侧的
    # 候选并集收好,再一次性查译名和队徽——同 team_style_preview.py
    # 194-199 行的既有范式,不在循环里逐队重复调用。
    pending_groups: list[dict[str, Any]] = []
    highlight_rows: list[Highlight] = []
    for group_key, keys in GROUPS.items():
        metrics: list[MetricProfileDTO] = []
        home_pcts: list[int | None] = []
        away_pcts: list[int | None] = []
        for key in keys:
            meta: MetricDef = get_metric(key)
            lower_is_better = meta.direction == "lower_better"

            home_row = home_dist.get(home_id, {}).get(key)
            away_row = away_dist.get(away_id, {}).get(key)
            home_value = home_row.value if home_row else None
            away_value = away_row.value if away_row else None

            home_others = [
                row[key].value for tid, row in home_dist.items()
                if tid != home_id and key in row and row[key].value is not None
            ]
            away_others = [
                row[key].value for tid, row in away_dist.items()
                if tid != away_id and key in row and row[key].value is not None
            ]
            home_pct = percentile_of(home_value, home_others, lower_is_better=lower_is_better)
            away_pct = percentile_of(away_value, away_others, lower_is_better=lower_is_better)
            home_pcts.append(home_pct)
            away_pcts.append(away_pct)

            metrics.append(MetricProfileDTO(
                key=key, name_zh=meta.name_zh, unit=meta.unit, direction=meta.direction,
                semantic=meta.semantic,
                home_value=round(home_value, 3) if home_value is not None else None,
                away_value=round(away_value, 3) if away_value is not None else None,
                home_percentile=home_pct, away_percentile=away_pct,
                home_complete=home_row.complete if home_row else False,
                away_complete=away_row.complete if away_row else False,
                league_sample_size=len(home_others) if home_pct is not None else len(away_others),
            ))

            if home_pct is not None and away_pct is not None:
                gap = abs(home_pct - away_pct)
                if gap >= GAP_FLOOR and home_value is not None and away_value is not None:
                    highlight_rows.append(Highlight(
                        key=key, name_zh=meta.name_zh,
                        home_percentile=home_pct, away_percentile=away_pct, gap=gap,
                        home_value=round(home_value, 3), away_value=round(away_value, 3),
                    ))

        home_group = group_percentile(home_pcts)
        away_group = group_percentile(away_pcts)

        # 对标球队:同一组指标下,全联赛(该 venue 分布)每队各自的组级百分位,
        # 主队在主场分布里找、客队在客场分布里找——跟本组百分位本身用的是
        # 同一套分布,不跨场景混着找"相似"。
        home_all_pcts = _all_team_group_percentiles(home_dist, keys)
        away_all_pcts = _all_team_group_percentiles(away_dist, keys)
        home_peer_candidates = nearest_peers(home_id, home_all_pcts)
        away_peer_candidates = nearest_peers(away_id, away_all_pcts)

        pending_groups.append({
            "key": group_key, "metrics": metrics,
            "home_group_percentile": home_group.value, "away_group_percentile": away_group.value,
            "home_peer_candidates": home_peer_candidates, "away_peer_candidates": away_peer_candidates,
        })

    all_candidates = [
        c
        for pg in pending_groups
        for c in (*pg["home_peer_candidates"], *pg["away_peer_candidates"])
    ]
    peer_ids = {c.team_id for c in all_candidates}
    display = team_display_for(conn, peer_ids) if peer_ids else {}
    crest_map = {tid: resolve_team_crest_url("fotmob", tid) for tid in peer_ids}

    groups: list[GroupProfileDTO] = [
        GroupProfileDTO(
            key=pg["key"], title_zh=GROUP_TITLE_ZH[pg["key"]], metrics=pg["metrics"],
            home_group_percentile=pg["home_group_percentile"], away_group_percentile=pg["away_group_percentile"],
            home_peers=_resolve_peers(pg["home_peer_candidates"], display, crest_map),
            away_peers=_resolve_peers(pg["away_peer_candidates"], display, crest_map),
        )
        for pg in pending_groups
    ]

    return MatchDataProfileDTO(
        home_matches=home_matches, away_matches=away_matches,
        home_available=home_available, away_available=away_available,
        groups=groups, highlights=top_gaps(highlight_rows),
        unavailable_reason=None,
        scope_note=scope_note, venue_mode=venue_mode, window_n=max_n,
    )
