"""data_coverage.py 覆盖真源 CLI 的确定性与档位不变量(临时 SQLite)。"""

from __future__ import annotations

import json
import sqlite3

import pytest

from backend.cli.data_coverage import build_coverage, to_markdown


@pytest.fixture()
def dbs():
    core = sqlite3.connect(":memory:")
    core.row_factory = sqlite3.Row
    core.executescript("""
        CREATE TABLE dim_match (Match_ID INT PRIMARY KEY, League_ID INT, Season TEXT,
          status TEXT, kickoff_at_utc TEXT, kickoff_precision TEXT);
        CREATE TABLE int_match_features (Match_ID INT PRIMARY KEY);
        CREATE TABLE fact_team_match_stats (Match_ID INT);
        -- 1: full_timeline; 2: legacy only; 3: 无赔率; 4: 未开赛
        INSERT INTO dim_match VALUES
          (1, 47, '2024/2025', 'Finish', '2024-08-01T15:00:00Z', 'exact'),
          (2, 47, '2024/2025', 'Finish', '2024-08-02T15:00:00Z', 'exact'),
          (3, 47, '2024/2025', 'Finish', NULL, 'date_only'),
          (4, 47, '2025/2026', 'NotStarted', '2025-08-01T15:00:00Z', 'exact');
        INSERT INTO int_match_features VALUES (1);
        INSERT INTO fact_team_match_stats VALUES (1),(1),(2);
    """)

    odds = sqlite3.connect(":memory:")
    odds.row_factory = sqlite3.Row
    odds.executescript("""
        CREATE TABLE dim_match_xref (provider TEXT, provider_match_id TEXT,
          fotmob_match_id INT, review_status TEXT);
        CREATE TABLE bronze_ng_odds_snap (provider_match_id TEXT, market TEXT,
          market_phase TEXT, company_id TEXT, company_name TEXT);
        CREATE TABLE bronze_legacy_odds_summary (fotmob_match_id INT, market TEXT,
          source TEXT, provider TEXT);
        INSERT INTO dim_match_xref VALUES
          ('nowgoal', 'T1', 1, 'auto_ok'),
          ('nowgoal', 'T3', 3, 'needs_review');       -- 未确认映射,必须排除
        INSERT INTO bronze_ng_odds_snap VALUES
          ('T1', '1x2', 'pre_match', '177', 'pinnacle'),
          ('T1', '1x2', 'pre_match', '281', 'bet 365'),
          ('T1', 'ah',  'pre_match', '177', 'pinnacle'),   -- 非 1x2 不计
          ('T3', '1x2', 'pre_match', '177', 'pinnacle');   -- xref 未确认,不计
        INSERT INTO bronze_legacy_odds_summary VALUES
          (1, '1x2', 'asset_a_json', 'Bet365'),   -- 已有 full,归 full 档
          (2, '1x2', 'asset_a_json', 'Bet365');
    """)

    platform = sqlite3.connect(":memory:")
    platform.row_factory = sqlite3.Row
    platform.executescript("""
        CREATE TABLE prediction_snapshots (match_id INT, status TEXT,
          is_official INT, locked_at TEXT);
        CREATE TABLE prediction_evaluations (id INT);
        INSERT INTO prediction_snapshots VALUES
          (1, 'published', 1, '2024-08-01T10:00:00Z'),
          (2, 'draft', 0, NULL);
    """)
    yield core, odds, platform
    core.close(); odds.close(); platform.close()


class TestCoverage:
    def test_tiers_and_no_company_merge(self, dbs):
        cov = build_coverage(*dbs)
        row = next(r for r in cov["partitions"]
                   if r["league_id"] == 47 and r["season"] == "2024/2025")
        o = row["odds"]
        assert o["full_timeline_matches"] == 1          # match 1(T3 未确认被排除)
        assert o["open_close_only_matches"] == 1        # match 2
        assert o["unavailable_matches"] == 1            # match 3
        # 公司标签分开报告,181/177 不合并;非 1x2 市场不进来
        assert o["full_timeline_by_company"] == {"177:pinnacle": 1, "281:bet 365": 1}
        assert o["legacy_by_source"] == {"asset_a_json:Bet365": 2}

    def test_partition_counters(self, dbs):
        cov = build_coverage(*dbs)
        row = next(r for r in cov["partitions"]
                   if r["league_id"] == 47 and r["season"] == "2024/2025")
        assert (row["matches"], row["finished"], row["exact_kickoff"]) == (3, 3, 2)
        assert row["features_int_match"] == 1
        assert row["facts"]["fact_team_match_stats"] == 2   # distinct match 数,非行数
        assert row["official_locked_predictions"] == 1
        ns = next(r for r in cov["partitions"] if r["season"] == "2025/2026")
        assert (ns["finished"], ns["not_started"]) == (0, 1)

    def test_deterministic_output(self, dbs):
        a = json.dumps(build_coverage(*dbs), sort_keys=True)
        b = json.dumps(build_coverage(*dbs), sort_keys=True)
        assert a == b
        assert "generated" not in a and "/tmp" not in a and "data/" not in a

    def test_markdown_renders_all_partitions(self, dbs):
        cov = build_coverage(*dbs)
        md = to_markdown(cov)
        assert md.count("| 47 |") == 2
        assert "正式评估 0 条" in md
