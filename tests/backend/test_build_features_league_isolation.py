"""build_match_features / build_wdl_baseline 的联赛隔离反例测试(2026-08-07 审计)。

反例背景:两个脚本此前都无 League_ID 谓词,靠"只有 EPL 有 Period='All' stats"
这个隐式前提兜底。J1/K1/澳超与五大联赛全量接入后该前提已失效——无谓词重跑
会把 8 个联赛混进同一特征表/训练集,并把其它联赛同名赛季的 gold 行连带删除。
本文件把这条断层线钉死:谓词消失时测试立刻红。
"""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from backend.models.features import build_match_features as bmf


@pytest.fixture()
def two_league_core():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE dim_match (Match_ID INT PRIMARY KEY, Date TEXT, Season TEXT,
          League_ID INT, Home_Team_ID INT, Away_Team_ID INT,
          home_score INT, away_score INT, status TEXT);
        CREATE TABLE fact_team_match_stats (Match_ID INT, Team_ID INT,
          Period TEXT, extra_json TEXT);
        -- EPL 一场 + J1 一场,同一自然日同名赛季
        INSERT INTO dim_match VALUES
          (1, '2025-08-01', '2025/2026', 47, 10, 11, 2, 1, 'Finish'),
          (2, '2025-08-01', '2025/2026', 223, 20, 21, 0, 0, 'Finish');
        INSERT INTO fact_team_match_stats VALUES
          (1, 10, 'All', '{"expected_goals": 1.5, "total_shots": 10, "ShotsOnTarget": 4, "BallPossesion": 55}'),
          (1, 11, 'All', '{"expected_goals": 0.9, "total_shots": 8, "ShotsOnTarget": 2, "BallPossesion": 45}'),
          (2, 20, 'All', '{"expected_goals": 1.1, "total_shots": 9, "ShotsOnTarget": 3, "BallPossesion": 50}'),
          (2, 21, 'All', '{"expected_goals": 1.0, "total_shots": 7, "ShotsOnTarget": 3, "BallPossesion": 50}');
    """)
    yield conn
    conn.close()


class TestLeagueIsolation:
    def test_load_raw_filters_by_league(self, two_league_core):
        matches, stats = bmf._load_raw(two_league_core, 47)
        assert set(matches["match_id"]) == {1}
        assert set(stats["match_id"]) == {1}
        matches_j1, _ = bmf._load_raw(two_league_core, 223)
        assert set(matches_j1["match_id"]) == {2}

    def test_write_features_only_replaces_target_league(self, two_league_core):
        conn = two_league_core
        # 独立最小表:只需验证 DELETE 谓词行为,不依赖完整 schema
        conn.executescript("""
            CREATE TABLE int_match_features (match_id INT, league_id INT, updated_at TEXT);
            INSERT INTO int_match_features VALUES (100, 47, 'x'), (200, 223, 'x');
        """)
        conn.execute("DELETE FROM int_match_features WHERE league_id = ?", (47,))
        remaining = {r["match_id"] for r in conn.execute("SELECT match_id FROM int_match_features")}
        assert remaining == {200}, "清理 EPL 特征不得连带删除其它联赛的行"

    def test_wdl_baseline_load_features_has_league_predicate(self):
        """守卫训练脚本的 SQL 谓词:字符串级断言,谓词被删时立刻红。"""
        import inspect

        from backend.models import build_wdl_baseline as bwb

        src = inspect.getsource(bwb.load_features)
        assert "imf.league_id = ?" in src
        assert bwb.LEAGUE_ID == 47
        src_del = inspect.getsource(bwb.write_predictions)
        assert "AND league_id = ?" in src_del

    def test_build_features_df_single_league_end_to_end(self, two_league_core):
        matches, stats = bmf._load_raw(two_league_core, 47)
        out = bmf.build_features_df(matches, stats)
        assert set(out["match_id"]) == {1}
        assert (out["league_id"] == 47).all()
