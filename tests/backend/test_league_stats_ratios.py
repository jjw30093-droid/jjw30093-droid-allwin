"""backend/queries/league_stats.py::team_season_stats 附带的 ratios 字段
(silver_team_season_ratios → TeamSeasonRatios DTO)。"""

from __future__ import annotations

import pytest

from backend.db.connections import connect_rw
from backend.queries import league_stats as q

from .coreseed import seed_basic_core

LEAGUE = 47
SEASON = "2025/2026"
TEAM = 1001


@pytest.fixture
def core_conn(data_dir):
    seed_basic_core(data_dir)
    conn = connect_rw("core")
    try:
        yield conn
    finally:
        conn.close()


def _insert_ratio(conn, league_id, season, team_id, metric_key, num, den, paired, played):
    conn.execute(
        "INSERT INTO silver_team_season_ratios (League_ID, Season, Team_ID, metric_key,"
        " numerator_sum, denominator_sum, paired_matches, matches_played, methodology_version)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'v2')",
        (league_id, season, team_id, metric_key, num, den, paired, played),
    )


class TestTeamRatios:
    def test_ratio_value_scaled_by_registry_display_scale(self, core_conn):
        """opp_half_pass_share 的 display_scale=100(默认值),value 必须已经
        ×100(不是原始比值)。"""
        _insert_ratio(core_conn, LEAGUE, SEASON, TEAM, "opp_half_pass_share", 180.0, 320.0, 38, 38)
        core_conn.commit()

        data = q.team_season_stats(core_conn, LEAGUE, SEASON)
        row = next(r for r in data["rows"] if r["team"]["team_id"] == TEAM)
        ratio = row["ratios"]["opp_half_pass_share"]
        assert ratio["value"] == pytest.approx(56.25)
        assert ratio["numerator"] == 180.0
        assert ratio["denominator"] == 320.0
        assert ratio["paired_matches"] == 38
        assert ratio["matches_played"] == 38

    def test_display_scale_is_not_inferred_from_unit_string(self, core_conn):
        """真实 bug 回归(2026-09-14):曾经用 `100.0 if unit=='%' else 1.0` 猜
        缩放,def_action_density 的 unit 是"次/百次"(不是字面 "%"),导致漏乘
        100,页面显示的"防守动作密度"少了两个数量级。改正后必须读
        MetricDef.display_scale,不再猜 unit 字符串。"""
        _insert_ratio(core_conn, LEAGUE, SEASON, TEAM, "def_action_density", 33.0, 400.0, 10, 10)
        core_conn.commit()

        data = q.team_season_stats(core_conn, LEAGUE, SEASON)
        row = next(r for r in data["rows"] if r["team"]["team_id"] == TEAM)
        ratio = row["ratios"]["def_action_density"]
        assert ratio["value"] == pytest.approx(8.25)  # 100 * 33/400,不是 0.0825

    def test_raw_value_metric_is_not_scaled_by_100(self, core_conn):
        """opp_xg_per_shot 的 display_scale=1.0(每脚 xG 本身就是展示值,
        不是百分比)——同一份代码路径,验证反方向也对。"""
        _insert_ratio(core_conn, LEAGUE, SEASON, TEAM, "opp_xg_per_shot", 15.0, 120.0, 10, 10)
        core_conn.commit()

        data = q.team_season_stats(core_conn, LEAGUE, SEASON)
        row = next(r for r in data["rows"] if r["team"]["team_id"] == TEAM)
        ratio = row["ratios"]["opp_xg_per_shot"]
        assert ratio["value"] == pytest.approx(0.125)  # 15/120,不是 12.5

    def test_no_paired_matches_gives_none_not_zero(self, core_conn):
        """分母为 0 或缺配对场次时 value=None,不是 0——0 在占比语境里是有意义的
        真实值,不能拿缺失当 0。"""
        _insert_ratio(core_conn, LEAGUE, SEASON, TEAM, "opp_half_pass_share", 0.0, 0.0, 0, 38)
        core_conn.commit()

        data = q.team_season_stats(core_conn, LEAGUE, SEASON)
        row = next(r for r in data["rows"] if r["team"]["team_id"] == TEAM)
        assert row["ratios"]["opp_half_pass_share"]["value"] is None

    def test_team_without_any_ratio_row_gets_none_ratios(self, core_conn):
        """该队在 silver_team_season_ratios 里完全没有行(比如刚好没有任何指标
        配对成功)—— ratios 整体是 None,不是一个全字段为 0 的对象。"""
        data = q.team_season_stats(core_conn, LEAGUE, SEASON)
        row = next(r for r in data["rows"] if r["team"]["team_id"] == TEAM)
        assert row["ratios"] is None

    def test_existing_seventeen_fields_unaffected(self, core_conn):
        """加 ratios 不改变任何既有字段的值——象限图三个既有视角零回归。"""
        _insert_ratio(core_conn, LEAGUE, SEASON, TEAM, "opp_half_pass_share", 100.0, 200.0, 10, 10)
        core_conn.commit()

        data = q.team_season_stats(core_conn, LEAGUE, SEASON)
        row = next(r for r in data["rows"] if r["team"]["team_id"] == TEAM)
        assert row["avg_expected_goals"] == 2.11
        assert row["avg_expected_goals_open_play"] == 1.42
        assert row["avg_expected_goals_set_play"] == 0.54
        assert row["matches_played"] == 38
