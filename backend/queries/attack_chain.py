"""图1·进攻转化链(PREMATCH_MOBILE_DATA_VISUALIZATION_V2 Phase 2.1,
验收返工一:补真实转化率语义)。

回答两个不同的问题,拆成两组明确分开的字段(手机端也要求分开展示,不要
堆成一串同质的数字):
- **进攻产量**(`volume_keys`):进攻半场传球占比、禁区触球、射门、射正、
  xG、xGOT——各自独立的窗口场均值。
- **转化效率**(`conversion_keys`):每 100 次禁区触球产生多少射门、射正率
  (射正/射门)、每脚射门 xG(xG/射门)、每次射正 xGOT(xGOT/射正)——
  **每一项都是同一场比赛内两个字段的配对相除后再对窗口求和**(不是两个
  独立窗口均值相除),用 `_ratio_field` 统一实现,与占比类产量指标同一套
  纪律。

用 `backend/queries/window.py` 的同主客场分级窗口聚合
`fact_team_match_stats.extra_json`(N=10 是 Phase 1.3 一次按时间切分的
样本外回测支持的取值,不是独立最终验证的最优窗口长度,细节见该模块注释)。

诚实纪律(沿用 Phase 0.2 的教训,不允许重犯):
- xG/xGOT 等字段缺失时该字段 `value=None`、`complete=False`,不补 0、不用
  "已知场次"重新归一化出一个看似完整的合计——调用方(DTO/前端)必须原样
  透出这两个字段,不能自己再求和拼接。
- 占比/转化率类指标用"窗口内分子分母各自总和之比",且分子分母必须来自
  **同一批场次**(逐场配对后再求和,不是分子分母各自独立求和再相除,
  避免把不同场次集合混进同一个比例);分母为 0(如某场零射门)的那一场
  不计入配对,不产生一个虚假的"0 分之几"比率;某类型窗口内一场配对都没有
  时该项 `value=None`,不是 0。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .window import DEFAULT_MAX_N, DEFAULT_MIN_N, venue_window


def _avg_field(conn: sqlite3.Connection, match_ids: list[int], team_id: int, field: str) -> tuple[float | None, int]:
    if not match_ids:
        return None, 0
    placeholders = ",".join("?" for _ in match_ids)
    rows = conn.execute(
        f"""SELECT json_extract(extra_json, '$.' || ?) v FROM fact_team_match_stats
             WHERE Team_ID=? AND Period='All' AND Match_ID IN ({placeholders})""",
        [field, team_id, *match_ids],
    ).fetchall()
    vals = [r["v"] for r in rows if r["v"] is not None]
    if not vals:
        return None, 0
    return sum(vals) / len(vals), len(vals)


def _ratio_field(
    conn: sqlite3.Connection, match_ids: list[int], team_id: int, numerator: str, denominator: str,
    *, scale: float = 100.0,
) -> tuple[float | None, int]:
    """分子分母来自同一行(同一场比赛)配对后再对窗口求和——不是两个独立
    均值相除。分母<=0 的场次不计入配对(不产生虚假的"0 分之几"比率)。
    `scale=100` 给百分比类(占比/命中率);转化效率里的"每脚 xG"这类单位
    本身就是比值,传 `scale=1` 不再乘 100。"""
    if not match_ids:
        return None, 0
    placeholders = ",".join("?" for _ in match_ids)
    rows = conn.execute(
        f"""SELECT json_extract(extra_json, '$.' || ?) num, json_extract(extra_json, '$.' || ?) den
              FROM fact_team_match_stats
             WHERE Team_ID=? AND Period='All' AND Match_ID IN ({placeholders})""",
        [numerator, denominator, team_id, *match_ids],
    ).fetchall()
    pairs = [(r["num"], r["den"]) for r in rows if r["num"] is not None and r["den"] is not None and r["den"] > 0]
    if not pairs:
        return None, 0
    num_sum = sum(p[0] for p in pairs)
    den_sum = sum(p[1] for p in pairs)
    return scale * num_sum / den_sum, len(pairs)


def team_attack_chain(
    conn: sqlite3.Connection, team_id: int, league_id: int, before_boundary: str,
    *, is_home: bool, max_n: int = DEFAULT_MAX_N, min_n: int = DEFAULT_MIN_N,
) -> dict[str, Any]:
    """该队(按主/客场景)进攻转化链五个环节的窗口均值,窗口本身来自
    `venue_window()`(分级 fallback,`tier`/`label_zh` 原样透出给前端标注档位)。
    """
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
        "volume_keys": ["opp_half_pass_share", "touches_opp_box", "shots", "shots_on_target", "xg", "xgot"],
        "conversion_keys": ["shots_per_100_box_touches", "shot_on_target_rate", "xg_per_shot", "xgot_per_sot"],
        # 进攻产量:各自独立的窗口场均值。
        "opp_half_pass_share": _metric(_ratio_field(conn, ids, team_id, "opposition_half_passes", "passes"), decimals=1),
        "touches_opp_box": _metric(_avg_field(conn, ids, team_id, "touches_opp_box")),
        "shots": _metric(_avg_field(conn, ids, team_id, "total_shots")),
        "shots_on_target": _metric(_avg_field(conn, ids, team_id, "ShotsOnTarget")),
        "xg": _metric(_avg_field(conn, ids, team_id, "expected_goals")),
        "xgot": _metric(_avg_field(conn, ids, team_id, "expected_goals_on_target")),
        # 转化效率:同一场比赛内两个字段配对相除后再对窗口求和,分母为 0
        # 或缺失的场次不计入配对,配对场次为 0 时诚实给 None(见模块 docstring)。
        "shots_per_100_box_touches": _metric(
            _ratio_field(conn, ids, team_id, "total_shots", "touches_opp_box"), decimals=1,
        ),
        "shot_on_target_rate": _metric(
            _ratio_field(conn, ids, team_id, "ShotsOnTarget", "total_shots"), decimals=1,
        ),
        "xg_per_shot": _metric(
            _ratio_field(conn, ids, team_id, "expected_goals", "total_shots", scale=1.0), decimals=3,
        ),
        "xgot_per_sot": _metric(
            _ratio_field(conn, ids, team_id, "expected_goals_on_target", "ShotsOnTarget", scale=1.0), decimals=3,
        ),
    }
