"""球队近 N 场历史聚合(赛前市场卡的唯一数据地基)。

背景:所有赛后事实表(fact_team_match_stats/fact_shotmap/fact_match_events/
fact_player_match_stats/fact_match_lineup/int_match_features)对
status='NotStarted' 的比赛精确为 0 行——未开赛比赛唯一能用的"这场比赛特有"
数据只有模型预测(未来 7 天覆盖 0 场)和 NowGoal 赔率(未来 7 天 47/78 场)。
第三条路是把两队各自的历史比赛聚合成赛前画像,这是本模块的全部职责。

字段沿用 backend/queries/match_report.py::TEAM_STAT_KEYS_CORE 同一份白名单
——那是已经对真实库全量核对过的核心 37 个 key,本模块不重新定义一份可能
漂移的副本。2026-08-25 起 match_report 另有 PHYSICAL/ZONES 两份低覆盖
白名单(体能仅英超少量场次、进攻区域为新采集),那些只进单场报告投影,
刻意不进本模块的近 N 场聚合——算进来永远是 n=0 的空指标,纯噪声。是否对匿名/免费用户可见由调用方(API 路由层)按 entitlement 投影,
本模块只管"数据本身是什么",不做权限判断。

诚实纪律(不可放松):
- 缺任一场次的某项指标就跳过那一场,不补 0——0 是"这场比赛真实统计出的
  某个指标为 0"的合法值(比如角球 0),不能拿来表示"没有数据"。
- 每项指标的样本量各自独立返回。touches_opp_box 全库只有 89.05% 覆盖,
  不能和 100% 覆盖的指标共用一个"共 N 场"的说法。
- 样本不足(<3 场)的指标返回 avg=None 而不是一个基于 1-2 场的假均值。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Literal

from backend.queries.match_report import TEAM_STAT_KEYS_CORE as TEAM_STAT_KEYS

MIN_SAMPLE = 3
Scope = Literal["same_league", "all"]
Period = Literal["All", "FirstHalf", "SecondHalf"]


def _extract(extra_json: str | None, source_key: str) -> float | None:
    if not extra_json:
        return None
    try:
        val = json.loads(extra_json).get(source_key)
    except (json.JSONDecodeError, AttributeError):
        return None
    return float(val) if isinstance(val, (int, float)) else None


def _recent_match_rows(
    conn: sqlite3.Connection,
    team_id: int,
    *,
    before_date: str,
    n: int,
    scope: Scope,
    league_id: int | None,
) -> list[sqlite3.Row]:
    league_clause = "AND League_ID=?" if scope == "same_league" else ""
    params: list[int | str] = [before_date, team_id, team_id]
    if scope == "same_league":
        if league_id is None:
            raise ValueError("scope='same_league' 需要显式传 league_id")
        params.append(league_id)
    params.append(n)
    return conn.execute(
        f"""SELECT Match_ID, Date, Home_Team_ID, Away_Team_ID
              FROM dim_match
             WHERE status IN ('Finish','Finished')
               AND Date < ?
               AND (Home_Team_ID=? OR Away_Team_ID=?)
               {league_clause}
             ORDER BY Date DESC, Match_ID DESC
             LIMIT ?""",
        params,
    ).fetchall()


def team_recent_profile(
    conn: sqlite3.Connection,
    team_id: int,
    *,
    before_date: str,
    n: int = 10,
    scope: Scope = "same_league",
    league_id: int | None = None,
    period: Period = "All",
) -> dict:
    """两队都要各自调用一次(本函数只算一支球队)。

    返回结构::

        {
          "team_id": 1001, "period": "All", "scope": "same_league", "window": 10,
          "matches_considered": 8,     # 候选比赛数(可能 < n,球队历史不够时)
          "match_ids": [...],
          "metrics": {
            "corners": {"for": {"avg": 5.25, "n": 8}, "against": {"avg": 4.1, "n": 8}},
            "touches_opp_box": {"for": {"avg": None, "n": 2}, "against": {...}},  # 样本不足
            ...
          },
        }

    ``matches_considered`` 是候选场次数,不是每项指标的真实样本量——各指标
    自己的 ``n`` 才是"这个数字实际算了几场"。调用方(市场卡)展示样本量时
    必须用指标自己的 n,不能用 matches_considered 冒充。
    """
    match_rows = _recent_match_rows(
        conn, team_id, before_date=before_date, n=n, scope=scope, league_id=league_id
    )
    if not match_rows:
        return {
            "team_id": team_id,
            "period": period,
            "scope": scope,
            "window": n,
            "matches_considered": 0,
            "match_ids": [],
            "metrics": {
                dto_key: {"for": {"avg": None, "n": 0}, "against": {"avg": None, "n": 0}}
                for dto_key in TEAM_STAT_KEYS.values()
            },
        }

    match_ids = [int(r["Match_ID"]) for r in match_rows]
    opponent_of = {
        int(r["Match_ID"]): int(r["Away_Team_ID"])
        if int(r["Home_Team_ID"]) == team_id
        else int(r["Home_Team_ID"])
        for r in match_rows
    }

    placeholders = ",".join("?" for _ in match_ids)
    stat_rows = conn.execute(
        f"""SELECT Match_ID, Team_ID, extra_json
              FROM fact_team_match_stats
             WHERE Period=? AND Match_ID IN ({placeholders})""",
        [period, *match_ids],
    ).fetchall()
    by_key: dict[tuple[int, int], str | None] = {
        (int(r["Match_ID"]), int(r["Team_ID"])): r["extra_json"] for r in stat_rows
    }

    metrics: dict[str, dict] = {}
    for source_key, dto_key in TEAM_STAT_KEYS.items():
        for_vals: list[float] = []
        against_vals: list[float] = []
        for mid in match_ids:
            opp = opponent_of[mid]
            fv = _extract(by_key.get((mid, team_id)), source_key)
            av = _extract(by_key.get((mid, opp)), source_key)
            if fv is not None:
                for_vals.append(fv)
            if av is not None:
                against_vals.append(av)

        def _summary(vals: list[float]) -> dict:
            if len(vals) < MIN_SAMPLE:
                return {"avg": None, "n": len(vals)}
            return {"avg": round(sum(vals) / len(vals), 3), "n": len(vals)}

        metrics[dto_key] = {"for": _summary(for_vals), "against": _summary(against_vals)}

    return {
        "team_id": team_id,
        "period": period,
        "scope": scope,
        "window": n,
        "matches_considered": len(match_ids),
        "match_ids": match_ids,
        "metrics": metrics,
    }
