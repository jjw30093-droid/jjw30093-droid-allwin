"""图3·防守承压与限制能力(PREMATCH_MOBILE_DATA_VISUALIZATION_V2 Phase 2.3)。

回答「这支球队让对手打出了多少威胁,限制能力怎么样」——被射门 · 被射正 ·
xGA(让出 xG) · 禁区内被射门,四项全部来自**对手在同一场比赛里的数值**
(同 `backend/queries/opponent_adjust.py::_team_field_history_avg(conceded=True)`
的对手 join 写法,但这里窗口已经由 `venue_window()` 选好,不需要重新做
PIT 历史平均,只需在已选定的 match_ids 上取对手值)。

诚实纪律(方案 §三·图3):拦截/解围/封堵是防守动作或风格,不是防守结果,
本模块不产出这些字段——不给"解围多=防守强"这种误导留任何空间,也不
合成神秘防守评分。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .window import DEFAULT_MAX_N, DEFAULT_MIN_N, venue_window


def _avg_opponent_field(
    conn: sqlite3.Connection, match_ids: list[int], team_id: int, field: str,
) -> tuple[float | None, int]:
    """`team_id` 在这些比赛里,对手(同场反向球队)该字段的均值——用来表达
    "对手在这些比赛里打出了多少",即 team_id 一方"面对/被"的量。"""
    if not match_ids:
        return None, 0
    placeholders = ",".join("?" for _ in match_ids)
    rows = conn.execute(
        f"""SELECT json_extract(t_opp.extra_json, '$.' || ?) v
              FROM dim_match m
              JOIN fact_team_match_stats t_opp ON t_opp.Match_ID=m.Match_ID AND t_opp.Period='All'
               AND t_opp.Team_ID = (CASE WHEN m.Home_Team_ID=? THEN m.Away_Team_ID ELSE m.Home_Team_ID END)
             WHERE m.Match_ID IN ({placeholders})""",
        [field, team_id, *match_ids],
    ).fetchall()
    vals = [r["v"] for r in rows if r["v"] is not None]
    if not vals:
        return None, 0
    return sum(vals) / len(vals), len(vals)


def team_defensive_pressure(
    conn: sqlite3.Connection, team_id: int, league_id: int, before_boundary: str,
    *, is_home: bool, max_n: int = DEFAULT_MAX_N, min_n: int = DEFAULT_MIN_N,
) -> dict[str, Any]:
    w = venue_window(conn, team_id, league_id, before_boundary, is_home=is_home, max_n=max_n, min_n=min_n)
    ids = w.match_ids
    n = w.matches

    def _metric(value_n: tuple[float | None, int], decimals: int = 2) -> dict[str, Any]:
        value, k = value_n
        return {
            "value": round(value, decimals) if value is not None else None,
            "complete": n > 0 and k == n,
            "matches_with_data": k,
        }

    return {
        "tier": w.tier,
        "matches": n,
        "label_zh": w.label_zh,
        "shots_faced": _metric(_avg_opponent_field(conn, ids, team_id, "total_shots"), decimals=1),
        "shots_on_target_faced": _metric(_avg_opponent_field(conn, ids, team_id, "ShotsOnTarget"), decimals=1),
        "xga": _metric(_avg_opponent_field(conn, ids, team_id, "expected_goals")),
        "box_shots_faced": _metric(_avg_opponent_field(conn, ids, team_id, "shots_inside_box"), decimals=1),
    }
