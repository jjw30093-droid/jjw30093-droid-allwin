"""图2·控球与场面控制(PREMATCH_MOBILE_DATA_VISUALIZATION_V2 Phase 2.2)。

回答「有控球是不是真能推进到威胁区,还是空转」——控球率、传球成功率、
进攻半场传球占比、禁区触球四条对比,复用 `attack_chain.py` 的字段聚合
helper(同一套 PIT-safe 窗口 + 分子分母配对逻辑,不重写一遍 SQL)。

**命名红线**(方案 §三·图2):`opposition_half_passes ÷ passes` 一律叫
「进攻半场传球占比」,不得冒充 Opta/StatsBomb 的正式 Field Tilt——同
`backend/metrics/registry.py` 里 `opp_half_pass_share` 的解释文案。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .attack_chain import _avg_field, _ratio_field
from .window import DEFAULT_MAX_N, DEFAULT_MIN_N, venue_window


def team_possession_control(
    conn: sqlite3.Connection, team_id: int, league_id: int, before_boundary: str,
    *, is_home: bool, max_n: int = DEFAULT_MAX_N, min_n: int = DEFAULT_MIN_N,
) -> dict[str, Any]:
    w = venue_window(conn, team_id, league_id, before_boundary, is_home=is_home, max_n=max_n, min_n=min_n)
    ids = w.match_ids
    n = w.matches

    def _metric(value_n: tuple[float | None, int], decimals: int = 1) -> dict[str, Any]:
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
        "possession": _metric(_avg_field(conn, ids, team_id, "BallPossesion")),
        "pass_accuracy": _metric(_ratio_field(conn, ids, team_id, "accurate_passes", "passes")),
        "opp_half_pass_share": _metric(_ratio_field(conn, ids, team_id, "opposition_half_passes", "passes")),
        "touches_opp_box": _metric(_avg_field(conn, ids, team_id, "touches_opp_box")),
    }
