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
from typing import Any

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


def _venue_col(is_home: bool) -> str:
    # 内部固定字符串,不接收外部输入——同 team_style_preview.py 的既有写法。
    return "m.Home_Team_ID" if is_home else "m.Away_Team_ID"


def _ranked_cte(is_home: bool, *, with_opponent: bool) -> str:
    opp_col = ", CASE WHEN t.Team_ID=m.Home_Team_ID THEN m.Away_Team_ID ELSE m.Home_Team_ID END opp_id" if with_opponent else ""
    return f"""
      SELECT t.Team_ID tid, t.Match_ID mid, t.extra_json extra_json{opp_col},
             ROW_NUMBER() OVER (
               PARTITION BY t.Team_ID
               ORDER BY COALESCE(m.kickoff_at_utc, m.Date) DESC, m.Match_ID DESC
             ) rn
        FROM dim_match m
        JOIN fact_team_match_stats t ON t.Match_ID=m.Match_ID AND t.Period='All'
                                     AND t.Team_ID={_venue_col(is_home)}
       WHERE m.League_ID=? AND m.status IN ('Finish','Finished')
         AND COALESCE(m.kickoff_at_utc, m.Date) < ?
         AND COALESCE(m.kickoff_at_utc, m.Date) >= ?
    """


def league_window_matches(
    conn: sqlite3.Connection, league_id: int, before_boundary: str, *, is_home: bool, max_n: int = DEFAULT_MAX_N,
) -> dict[int, list[int]]:
    """每支球队近 max_n 场同 venue 比赛的 match_id 列表(全联赛一次查完)。

    仅用于测试断言与 `_league_own_avg_batch` 之类函数共享同一份选窗口逻辑
    ——真正的字段聚合走各自的批量函数,不逐队复用这份 match_id 再单独查询
    (那样又变回 N+1)。
    """
    floor_ = _lookback_floor(before_boundary)
    sql = f"""
      WITH ranked AS ({_ranked_cte(is_home, with_opponent=False)})
      SELECT tid, mid FROM ranked WHERE rn<=?
      ORDER BY tid, mid
    """
    rows = conn.execute(sql, (league_id, before_boundary, floor_, max_n)).fetchall()
    out: dict[int, list[int]] = {}
    for r in rows:
        out.setdefault(int(r["tid"]), []).append(int(r["mid"]))
    return out


def _league_own_avg_batch(
    conn: sqlite3.Connection, league_id: int, before_boundary: str, floor_: str, *,
    is_home: bool, max_n: int, fields: dict[str, str],
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
      WITH ranked AS ({_ranked_cte(is_home, with_opponent=False)}),
      last_n AS (SELECT tid, extra_json FROM ranked WHERE rn<=?)
      SELECT tid, COUNT(*) n, {cols} FROM last_n GROUP BY tid
    """
    rows = conn.execute(sql, (league_id, before_boundary, floor_, max_n)).fetchall()
    out: dict[int, dict[str, TeamMetricValue]] = {}
    for r in rows:
        n = r["n"]
        out[int(r["tid"])] = {
            k: TeamMetricValue(value=r[f"v_{k}"], matches_with_data=r[f"k_{k}"] or 0, window_matches=n)
            for k in fields
        }
    return out


def _league_opponent_avg_batch(
    conn: sqlite3.Connection, league_id: int, before_boundary: str, floor_: str, *,
    is_home: bool, max_n: int, fields: dict[str, str],
) -> dict[int, dict[str, TeamMetricValue]]:
    if not fields:
        return {}
    cols = ", ".join(
        f"AVG(json_extract(opp.extra_json,'$.{f}')) v_{k}, COUNT(json_extract(opp.extra_json,'$.{f}')) k_{k}"
        for k, f in fields.items()
    )
    sql = f"""
      WITH ranked AS ({_ranked_cte(is_home, with_opponent=True)}),
      last_n AS (SELECT tid, mid, opp_id FROM ranked WHERE rn<=?)
      SELECT l.tid tid, COUNT(*) n, {cols}
        FROM last_n l
        JOIN fact_team_match_stats opp ON opp.Match_ID=l.mid AND opp.Team_ID=l.opp_id AND opp.Period='All'
       GROUP BY l.tid
    """
    rows = conn.execute(sql, (league_id, before_boundary, floor_, max_n)).fetchall()
    out: dict[int, dict[str, TeamMetricValue]] = {}
    for r in rows:
        n = r["n"]
        out[int(r["tid"])] = {
            k: TeamMetricValue(value=r[f"v_{k}"], matches_with_data=r[f"k_{k}"] or 0, window_matches=n)
            for k in fields
        }
    return out


def _league_ratio_batch(
    conn: sqlite3.Connection, league_id: int, before_boundary: str, floor_: str, *,
    is_home: bool, max_n: int, fields: dict[str, tuple[str, str]], scale: float = 100.0,
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
      WITH ranked AS ({_ranked_cte(is_home, with_opponent=False)}),
      last_n AS (SELECT tid, extra_json FROM ranked WHERE rn<=?)
      SELECT tid, COUNT(*) n, {cols} FROM last_n GROUP BY tid
    """
    rows = conn.execute(sql, (league_id, before_boundary, floor_, max_n)).fetchall()
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
    is_home: bool, max_n: int = DEFAULT_MAX_N, min_n: int = DEFAULT_MIN_N,
) -> dict[int, dict[str, TeamMetricValue]]:
    """全联赛(该 venue)每支球队的 12 项指标——6 条批量 SQL(own-avg / opponent-avg
    / ratio,各一条),不是逐队逐字段查询。样本不足 `min_n` 的球队从返回结果里
    整体剔除(不产出该队任何字段,不是产出 `value=None` 的空壳)——这样调用方
    在构建"其它球队分布"时只需过滤 `team_id != 自身`,不需要再检查 tier。
    """
    floor_ = _lookback_floor(before_boundary)
    own = _league_own_avg_batch(conn, league_id, before_boundary, floor_, is_home=is_home, max_n=max_n, fields=_OWN_AVG_FIELDS)
    opp = _league_opponent_avg_batch(conn, league_id, before_boundary, floor_, is_home=is_home, max_n=max_n, fields=_OPPONENT_AVG_FIELDS)
    ratio = _league_ratio_batch(conn, league_id, before_boundary, floor_, is_home=is_home, max_n=max_n, fields=_RATIO_FIELDS)

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


def match_data_profile(
    conn: sqlite3.Connection, league_id: int, before_boundary: str, home_id: int, away_id: int,
    *, max_n: int = DEFAULT_MAX_N, min_n: int = DEFAULT_MIN_N,
) -> MatchDataProfileDTO:
    """本场两队的攻/守/控三组联赛百分位画像。

    主队对**联赛主场分布**取百分位,客队对**联赛客场分布**取百分位——两套
    独立分布,不共用同一个原始联赛均值(主客场系统性差异 17~18%,见
    `window.py` 文档)。两队因此不在同一条绝对数值尺上,但在各自的百分位尺
    上可比,前端措辞必须讲清楚这一点。
    """
    home_dist = league_metric_distribution(conn, league_id, before_boundary, is_home=True, max_n=max_n, min_n=min_n)
    away_dist = league_metric_distribution(conn, league_id, before_boundary, is_home=False, max_n=max_n, min_n=min_n)

    home_matches = _team_window_matches(home_dist, home_id)
    away_matches = _team_window_matches(away_dist, away_id)
    home_available = home_id in home_dist
    away_available = away_id in away_dist

    if not home_available and not away_available:
        return MatchDataProfileDTO(
            home_matches=home_matches, away_matches=away_matches,
            home_available=False, away_available=False, groups=[], highlights=[],
            unavailable_reason="两队本赛季同主客场比赛都不足,暂无法给出联赛百分位画像。",
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
    )
