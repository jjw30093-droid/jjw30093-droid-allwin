"""backend/silver/player_season.py:联赛球员象限图的赛季聚合(2026-09-15)。

只测这一层的接线是否正确(per-90 换算、比率换算、CBIRT 多列相加不连坐、
转会球员的 team_minutes 取 MAX、点球扣减)——具体指标口径的生产数据实测
结论已经写进方案文档,这里用小型可控 fixture 验证代码逻辑本身。
"""

from __future__ import annotations

import json

import pytest

from backend.db.connections import connect_rw
from tests.backend.coreseed import insert_match, seed_core_schema
from backend.silver.player_season import (
    build_player_season_ratios,
    build_player_season_stats,
    PLAYER_METRIC_SPECS,
)

LEAGUE = 47
SEASON = "2025/2026"


def _stats(conn, match_id, player_id, team_id, **fields):
    cols = ["Match_ID", "Player_ID", "Team_ID"] + list(fields.keys())
    quoted = [f'"{c}"' if "." in c else c for c in cols]
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO fact_player_match_stats ({', '.join(quoted)}) VALUES ({placeholders})",
        [match_id, player_id, team_id, *fields.values()],
    )


@pytest.fixture
def core_conn(data_dir):
    conn = connect_rw("core")
    seed_core_schema(conn)
    yield conn
    conn.close()


class TestBuildPlayerSeasonStats:
    def test_minutes_and_appearances_summed_across_matches(self, core_conn):
        for i in range(3):
            mid = 8300 + i
            insert_match(
                core_conn, mid, league_id=LEAGUE, season=SEASON, date=f"2025-09-{10+i:02d}",
                home_id=1001, away_id=1002, status="Finish", home_score=1, away_score=0,
            )
            _stats(core_conn, mid, "p1", 1001, minutes_played=90, usual_position="2", player_name="Test MF")
        core_conn.commit()

        rows = {r["Player_ID"]: r for r in build_player_season_stats(core_conn, LEAGUE, SEASON)}
        assert rows["p1"]["appearances"] == 3
        assert rows["p1"]["minutes_played"] == 270
        assert rows["p1"]["usual_position"] == 2

    def test_team_minutes_uses_max_across_teams_for_transferred_player(self, core_conn):
        """转会球员:team_minutes 取 MAX(该队完赛场次×90),不是主队那支。
        球队 A 只踢了 1 场(90 分钟可用),球队 B 踢了 3 场(270 分钟可用)——
        即使球员在 A 队出场分钟更多(显示用的 Team_ID 应该是 A),分母仍必须
        取更严格的 A 队的 90(不是宽松的 B 队 270),因为口径是 MAX over
        每支队自己的完赛场次,而 A 只完赛 1 场。"""
        insert_match(
            core_conn, 8400, league_id=LEAGUE, season=SEASON, date="2025-08-10",
            home_id=1001, away_id=1002, status="Finish", home_score=1, away_score=0,
        )
        for i in range(3):
            mid = 8401 + i
            insert_match(
                core_conn, mid, league_id=LEAGUE, season=SEASON, date=f"2025-09-{10+i:02d}",
                home_id=1003, away_id=1004, status="Finish", home_score=1, away_score=0,
            )
        # 球员在 A 队(1001)出场 1 场 90 分钟,在 B 队(1003)出场 1 场 30 分钟
        _stats(core_conn, 8400, "p1", 1001, minutes_played=90, usual_position="2", player_name="Transfer")
        _stats(core_conn, 8401, "p1", 1003, minutes_played=30, usual_position="2", player_name="Transfer")
        core_conn.commit()

        rows = {r["Player_ID"]: r for r in build_player_season_stats(core_conn, LEAGUE, SEASON)}
        row = rows["p1"]
        assert row["Team_ID"] == 1001  # 出场分钟最多的那支队(90 > 30)
        assert row["teams_count"] == 2
        # A 队(1001)完赛 1 场 → 90;B 队(1003)完赛 3 场 → 270。MAX = 270。
        assert row["team_minutes"] == 270
        assert row["minutes_played"] == 120  # 90 + 30
        assert row["minutes_share"] == round(120 / 270, 4)  # build_player_season_stats 存的是 round(...,4)


class TestBuildPlayerSeasonRatios:
    def test_per90_metric_scales_by_minutes(self, core_conn):
        insert_match(
            core_conn, 8500, league_id=LEAGUE, season=SEASON, date="2025-09-10",
            home_id=1001, away_id=1002, status="Finish", home_score=1, away_score=0,
        )
        _stats(core_conn, 8500, "p1", 1001, minutes_played=45, chances_created=2, expected_assists=0.5)
        core_conn.commit()

        rows = build_player_season_ratios(core_conn, LEAGUE, SEASON)
        by_key = {(r["Player_ID"], r["metric_key"]): r for r in rows}
        cc = by_key[("p1", "chances_created_per90")]
        assert cc["numerator_sum"] == 2
        assert cc["denominator_sum"] == 45  # SUM(minutes_played),display_scale=90 换算留给读取层

    def test_cbirt_multi_column_sum_does_not_cascade_on_missing_field(self, core_conn):
        """CBIRT 五项分别 SUM 再相加(不是逐行相加)——某一场 shot_blocks 缺失
        不应该让整场的 tackles/interceptions/clearances/recoveries 也被拖累
        归零或排除(那正是团队侧逐场配对模型的连坐问题,球员侧刻意不这样做)。"""
        insert_match(
            core_conn, 8600, league_id=LEAGUE, season=SEASON, date="2025-09-10",
            home_id=1001, away_id=1002, status="Finish", home_score=1, away_score=0,
        )
        insert_match(
            core_conn, 8601, league_id=LEAGUE, season=SEASON, date="2025-09-17",
            home_id=1001, away_id=1002, status="Finish", home_score=1, away_score=0,
        )
        _stats(
            core_conn, 8600, "p1", 1001, minutes_played=90,
            **{"matchstats.headers.tackles": 2, "interceptions": 1, "clearances": 0, "shot_blocks": None, "recoveries": 3},
        )
        _stats(
            core_conn, 8601, "p1", 1001, minutes_played=90,
            **{"matchstats.headers.tackles": 1, "interceptions": 2, "clearances": 1, "shot_blocks": 1, "recoveries": 2},
        )
        core_conn.commit()

        rows = build_player_season_ratios(core_conn, LEAGUE, SEASON)
        by_key = {(r["Player_ID"], r["metric_key"]): r for r in rows}
        defact = by_key[("p1", "defensive_actions_per90")]
        # 场1: 2+1+0+0(shot_blocks NULL 当 0)+3=6;场2: 1+2+1+1+2=7;合计13
        assert defact["numerator_sum"] == 13
        assert defact["denominator_sum"] == 180

    def test_duel_win_rate_denominator_is_won_plus_lost(self, core_conn):
        insert_match(
            core_conn, 8700, league_id=LEAGUE, season=SEASON, date="2025-09-10",
            home_id=1001, away_id=1002, status="Finish", home_score=1, away_score=0,
        )
        _stats(core_conn, 8700, "p1", 1001, minutes_played=90, duel_won=6, duel_lost=4)
        core_conn.commit()

        rows = build_player_season_ratios(core_conn, LEAGUE, SEASON)
        by_key = {(r["Player_ID"], r["metric_key"]): r for r in rows}
        dw = by_key[("p1", "duel_win_rate")]
        assert dw["numerator_sum"] == 6
        assert dw["denominator_sum"] == 10

    def test_player_with_zero_duels_excluded_from_duel_win_rate(self, core_conn):
        """分母为 0(该球员整季没有任何对抗)的行必须被 HAVING 排除,不产出
        一个 0/0 的比率行。"""
        insert_match(
            core_conn, 8800, league_id=LEAGUE, season=SEASON, date="2025-09-10",
            home_id=1001, away_id=1002, status="Finish", home_score=1, away_score=0,
        )
        _stats(core_conn, 8800, "p1", 1001, minutes_played=90, duel_won=None, duel_lost=None)
        core_conn.commit()

        rows = build_player_season_ratios(core_conn, LEAGUE, SEASON)
        by_key = {(r["Player_ID"], r["metric_key"]) for r in rows}
        assert ("p1", "duel_win_rate") not in by_key

    def test_finishing_delta_subtracts_penalty_goals_via_shotmap(self, core_conn):
        """终结超额的非点球进球 = goals(核心列,含点球)− shotmap 里
        Situation=Penalty AND Outcome=Goal 的次数——不是简单用 goals 本身。"""
        insert_match(
            core_conn, 8900, league_id=LEAGUE, season=SEASON, date="2025-09-10",
            home_id=1001, away_id=1002, status="Finish", home_score=2, away_score=0,
        )
        _stats(core_conn, 8900, "p1", 1001, minutes_played=90, goals=2, expected_goals_non_penalty=0.8)
        core_conn.execute(
            "INSERT INTO fact_shotmap (Match_ID, Player_ID, Team_ID, Situation, Outcome, xG)"
            " VALUES (8900, 'p1', 1001, 'Penalty', 'Goal', 0.7884)"
        )
        core_conn.commit()

        rows = build_player_season_ratios(core_conn, LEAGUE, SEASON)
        by_key = {(r["Player_ID"], r["metric_key"]): r for r in rows}
        fd = by_key[("p1", "finishing_delta_per90")]
        # 非点球进球 = 2(goals)− 1(点球进球) = 1;减去 npxG(0.8) = 0.2
        assert fd["numerator_sum"] == pytest.approx(0.2)
        assert fd["denominator_sum"] == 90

    def test_all_metric_specs_registered_in_registry(self):
        """PLAYER_METRIC_SPECS 里每个 metric_key 都必须已在
        backend/metrics/registry.py 登记(get_metric 找不到就直接抛错)——
        与球队侧 test_metrics_registry.py 的接线测试同一个模式。"""
        from backend.metrics.registry import get_metric

        for spec in PLAYER_METRIC_SPECS:
            metric = get_metric(spec.metric_key)
            assert metric.display_scale in (90.0, 100.0)
        # finishing_delta_per90 单独处理,不在 PLAYER_METRIC_SPECS 里,但同样要登记
        get_metric("finishing_delta_per90")
