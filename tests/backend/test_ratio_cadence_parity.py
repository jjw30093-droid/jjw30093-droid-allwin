"""防止 silver_team_season_ratios(赛季尺度)与 attack_chain::_ratio_field
(窗口尺度)这两套独立实现的配对逻辑悄悄漂移——同一批比赛、同一支球队,
两条路径必须算出完全相同的数字(浮点误差范围内)。"""

from __future__ import annotations

import json

from backend.db.connections import connect_rw
from backend.queries.attack_chain import _ratio_field
from backend.silver.ratio_metrics import build_team_season_ratios
from tests.backend.coreseed import insert_match, seed_core_schema

LEAGUE = 47
SEASON = "2025/2026"
TEAM = 7201


def _stats(conn, match_id, team_id, **fields):
    conn.execute(
        "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
        " VALUES (?, ?, 'All', 0, ?)",
        (match_id, team_id, json.dumps(fields)),
    )


def test_season_aggregation_matches_ratio_field_on_same_matches(data_dir):
    conn = connect_rw("core")
    seed_core_schema(conn)
    match_ids = []
    # 有意造出不规则数据:有的场次分母缺失,数值也不是整除关系,
    # 这样才能真正检验两条实现路径是否用了同一套排除规则。
    fixtures = [
        (180.0, 320.0), (95.0, None), (240.0, 410.0), (60.0, 0.0), (310.0, 505.0),
        (140.0, 260.0), (None, 300.0), (88.0, 199.0),
    ]
    for j, (num, den) in enumerate(fixtures):
        mid = TEAM * 10 + j
        insert_match(
            conn, mid, league_id=LEAGUE, season=SEASON, date=f"2025-09-{10+j:02d}",
            home_id=TEAM, away_id=9500 + j, home="队A", away="路人",
            status="Finish", home_score=1, away_score=0,
            kickoff_at_utc=f"2025-09-{10+j:02d}T12:00:00Z",
        )
        match_ids.append(mid)
        fields = {}
        if num is not None:
            fields["opposition_half_passes"] = num
        if den is not None:
            fields["accurate_passes"] = den
        _stats(conn, mid, TEAM, **fields)
    conn.commit()

    window_value, window_k = _ratio_field(
        conn, match_ids, TEAM, "opposition_half_passes", "accurate_passes", scale=100.0
    )

    season_rows = build_team_season_ratios(conn, LEAGUE, SEASON, {TEAM: len(match_ids)})
    row = next(r for r in season_rows if r["Team_ID"] == TEAM)
    season_value = (
        100.0 * row["numerator_sum"] / row["denominator_sum"]
        if row["denominator_sum"]
        else None
    )

    assert row["paired_matches"] == window_k
    assert season_value == window_value
