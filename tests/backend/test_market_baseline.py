"""market_baseline 的时间纪律与口径不变量(任务规格最低测试要求)。"""

from __future__ import annotations

import sqlite3

import pytest

from backend.models.research.market_baseline import (
    build_full_timeline_baseline,
    build_summary_latest_baseline,
    proportional_devig,
)


@pytest.fixture()
def dbs():
    core = sqlite3.connect(":memory:")
    core.row_factory = sqlite3.Row
    core.executescript("""
        CREATE TABLE dim_match (Match_ID INT PRIMARY KEY, League_ID INT, status TEXT,
          kickoff_at_utc TEXT, kickoff_precision TEXT);
        INSERT INTO dim_match VALUES (1, 47, 'Finish', '2024-08-01T15:00:00Z', 'exact');
    """)
    odds = sqlite3.connect(":memory:")
    odds.row_factory = sqlite3.Row
    odds.executescript("""
        CREATE TABLE dim_match_xref (provider TEXT, provider_match_id TEXT,
          fotmob_match_id INT, review_status TEXT);
        CREATE TABLE bronze_ng_odds_snap (id INTEGER PRIMARY KEY, provider_match_id TEXT,
          market TEXT, market_phase TEXT, company_id TEXT, payload_json TEXT, observed_at TEXT);
        CREATE TABLE bronze_legacy_odds_summary (fotmob_match_id INT, market TEXT,
          period TEXT, source TEXT, home_or_over REAL, draw REAL, away_or_under REAL);
        INSERT INTO dim_match_xref VALUES ('nowgoal', 'T1', 1, 'auto_ok');
    """)
    yield core, odds
    core.close(); odds.close()


def _snap(odds, pmid, cid, payload, obs, phase="pre_match", market="1x2"):
    odds.execute(
        "INSERT INTO bronze_ng_odds_snap (provider_match_id, market, market_phase, company_id, payload_json, observed_at)"
        " VALUES (?,?,?,?,?,?)", (pmid, market, phase, cid, payload, obs))


class TestFullTimeline:
    def test_devig_proportional(self):
        ph, pd_, pa, over = proportional_devig(2.0, 3.0, 6.0)
        assert abs(ph + pd_ + pa - 1) < 1e-12
        assert abs(over - 1.0) < 1e-12
        assert abs(ph - 0.5) < 1e-12

    def test_last_strictly_pre_kickoff_wins(self, dbs):
        core, odds = dbs
        _snap(odds, 'T1', '281', '{"home": 2.0, "draw": 3.4, "away": 3.6}', '2024-08-01T10:00:00Z')
        _snap(odds, 'T1', '281', '{"home": 2.2, "draw": 3.3, "away": 3.3}', '2024-08-01T14:59:59Z')
        _snap(odds, 'T1', '281', '{"home": 9.0, "draw": 9.0, "away": 9.0}', '2024-08-01T15:00:00Z')  # == kickoff,拒
        b = build_full_timeline_baseline(core, odds)
        rec = b["per_company"]["281"][1]
        assert rec["observed_at"] == "2024-08-01T14:59:59Z"
        assert b["report"]["selection_stats"]["skipped_at_or_after_kickoff"] == 1

    def test_in_play_phase_never_enters(self, dbs):
        core, odds = dbs
        _snap(odds, 'T1', '281', '{"home": 2.0, "draw": 3.4, "away": 3.6}',
              '2024-08-01T14:00:00Z', phase='in_play')
        b = build_full_timeline_baseline(core, odds)
        assert b["per_company"]["281"] == {}

    def test_unmapped_match_fail_closed(self, dbs):
        core, odds = dbs
        odds.execute("UPDATE dim_match_xref SET review_status='needs_review'")
        _snap(odds, 'T1', '281', '{"home": 2.0, "draw": 3.4, "away": 3.6}', '2024-08-01T14:00:00Z')
        b = build_full_timeline_baseline(core, odds)
        assert b["per_company"]["281"] == {}
        assert b["report"]["selection_stats"]["skipped_unmapped"] == 1

    def test_companies_not_merged(self, dbs):
        core, odds = dbs
        _snap(odds, 'T1', '281', '{"home": 2.0, "draw": 3.4, "away": 3.6}', '2024-08-01T14:00:00Z')
        _snap(odds, 'T1', '177', '{"home": 2.1, "draw": 3.4, "away": 3.5}', '2024-08-01T14:30:00Z')
        b = build_full_timeline_baseline(core, odds)
        assert 1 in b["per_company"]["281"] and 1 in b["per_company"]["177"]
        assert b["per_company"]["281"][1]["p"] != b["per_company"]["177"][1]["p"]

    def test_bad_odds_and_overround_excluded(self, dbs):
        core, odds = dbs
        _snap(odds, 'T1', '281', '{"home": 1.0, "draw": 3.4, "away": 3.6}', '2024-08-01T14:00:00Z')
        b = build_full_timeline_baseline(core, odds)
        assert b["per_company"]["281"] == {}
        assert b["report"]["exclusions"]["281:bad_odds"] == 1


class TestSummaryLatest:
    def test_sources_reported_separately_and_no_timestamp_claim(self, dbs):
        core, odds = dbs
        odds.executescript("""
            INSERT INTO bronze_legacy_odds_summary VALUES
              (1, '1x2', 'latest', 'asset_a_json', 2.0, 3.4, 3.6),
              (1, '1x2', 'latest', 'asset_b_footballdata', 2.1, 3.3, 3.5),
              (1, '1x2', 'initial', 'asset_a_json', 1.9, 3.5, 3.8);
        """)
        b = build_summary_latest_baseline(core, odds)
        assert set(b["per_source"]) == {"asset_a_json", "asset_b_footballdata"}
        assert "closing" not in str(b["per_source"]).lower()
        assert "不得称 exact closing" in b["report"]["timestamp_evidence"]
