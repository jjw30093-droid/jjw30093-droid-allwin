from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.ingest.nowgoal_historical_odds import (
    historical_snap_records,
    ingest_historical_odds,
)

_SCHEMA_SQL = (
    Path(__file__).resolve().parents[2] / "backend" / "migrations" / "odds" / "0001_init.sql"
).read_text()


@pytest.fixture()
def odds_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "odds.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    yield conn
    conn.close()


AH_POINTS = [
    {"observed_at": "2025-08-13T15:00:00Z", "home": 0.93, "line": -0.75, "away": 0.88},
    {"observed_at": "2025-08-14T14:00:00Z", "home": 0.9, "line": -0.75, "away": 0.9},
]
OU_POINTS = [
    {"observed_at": "2025-08-13T15:00:00Z", "over": 0.88, "line": 3.25, "under": 0.93},
]
X12_POINTS = [
    {"observed_at": "2025-08-13T15:00:00Z", "home": 1.44, "draw": 4.75, "away": 6.50},
]


class TestHistoricalSnapRecordsNotInverted:
    def test_opening_pinned_to_first_point_per_market(self):
        recs = historical_snap_records(AH_POINTS, OU_POINTS, X12_POINTS, company_id="8", inverted=False)
        ah_recs = [r for r in recs if r["market"] == "ah"]
        assert ah_recs[0]["initial"] == {"home": 0.93, "line": -0.75, "away": 0.88}
        assert ah_recs[1]["initial"] == ah_recs[0]["initial"]  # 同一序列,opening 恒定
        assert ah_recs[0]["latest"] == {"home": 0.93, "line": -0.75, "away": 0.88}
        assert ah_recs[1]["latest"] == {"home": 0.9, "line": -0.75, "away": 0.9}

    def test_company_name_looked_up_from_id(self):
        recs = historical_snap_records(AH_POINTS, [], [], company_id="1", inverted=False)
        assert all(r["company_name"] == "Macauslot" for r in recs)

    def test_unknown_company_id_yields_empty_name_not_crash(self):
        recs = historical_snap_records(AH_POINTS, [], [], company_id="999", inverted=False)
        assert all(r["company_name"] == "" for r in recs)

    def test_empty_market_produces_no_records(self):
        recs = historical_snap_records([], [], [], company_id="8", inverted=False)
        assert recs == []


class TestHistoricalSnapRecordsInverted:
    """AH 反转必须交换双边并把线取负,OU 不受影响,1x2 交换 home/away——
    和 backend/providers/nowgoal.py::normalize_for_inversion() 的真实规则
    完全一致(这里是复用它,不是重新发明)。"""

    def test_ah_swaps_and_negates_line(self):
        recs = historical_snap_records(AH_POINTS, [], [], company_id="8", inverted=True)
        first = recs[0]
        assert first["initial"] == {"home": 0.88, "line": 0.75, "away": 0.93}
        assert first["latest"] == {"home": 0.88, "line": 0.75, "away": 0.93}

    def test_ou_unaffected_by_inversion(self):
        not_inv = historical_snap_records([], OU_POINTS, [], company_id="8", inverted=False)
        inv = historical_snap_records([], OU_POINTS, [], company_id="8", inverted=True)
        assert not_inv[0]["latest"] == inv[0]["latest"]

    def test_1x2_swaps_home_away(self):
        recs = historical_snap_records([], [], X12_POINTS, company_id="281", inverted=True)
        assert recs[0]["latest"] == {"home": 6.50, "draw": 4.75, "away": 1.44}


class TestIngestHistoricalOdds:
    def test_inserts_all_points_with_pre_match_phase(self, odds_db):
        recs = historical_snap_records(AH_POINTS, OU_POINTS, X12_POINTS, company_id="8", inverted=False)
        result = ingest_historical_odds(odds_db, "titan-1", recs, poll_run_id="test-run")
        odds_db.commit()
        assert result == {"inserted": len(recs), "skipped": 0}
        phases = [r[0] for r in odds_db.execute(
            "SELECT DISTINCT market_phase FROM bronze_ng_odds_snap").fetchall()]
        assert phases == ["pre_match"]

    def test_source_updated_at_and_observed_at_both_use_historical_timestamp(self, odds_db):
        """核心断言:这条数据从赛前几个月前抓回来,observed_at 不能是"现在",
        必须是来源自己声明的历史时刻,否则整条轨迹的时间信息会被抹平成一个点。"""
        recs = historical_snap_records(AH_POINTS, [], [], company_id="8", inverted=False)
        ingest_historical_odds(odds_db, "titan-1", recs, poll_run_id="test-run")
        odds_db.commit()
        rows = odds_db.execute(
            "SELECT source_updated_at, observed_at FROM bronze_ng_odds_snap ORDER BY observed_at"
        ).fetchall()
        assert rows[0] == ("2025-08-13T15:00:00Z", "2025-08-13T15:00:00Z")
        assert rows[1] == ("2025-08-14T14:00:00Z", "2025-08-14T14:00:00Z")

    def test_idempotent_on_rerun_by_existing_observed_at(self, odds_db):
        recs = historical_snap_records(AH_POINTS, OU_POINTS, X12_POINTS, company_id="8", inverted=False)
        first = ingest_historical_odds(odds_db, "titan-1", recs, poll_run_id="run-1")
        odds_db.commit()
        second = ingest_historical_odds(odds_db, "titan-1", recs, poll_run_id="run-2")
        odds_db.commit()
        assert first["inserted"] == len(recs)
        assert second == {"inserted": 0, "skipped": len(recs)}
        total = odds_db.execute("SELECT COUNT(*) FROM bronze_ng_odds_snap").fetchone()[0]
        assert total == len(recs)

    def test_different_companies_do_not_collide(self, odds_db):
        """同一场比赛、同一批时间戳,换一家公司必须各自独立落库,不能被
        (provider_match_id, market, observed_at) 误判成重复而漏写。"""
        bet365 = historical_snap_records(AH_POINTS, [], [], company_id="8", inverted=False)
        crown = historical_snap_records(AH_POINTS, [], [], company_id="3", inverted=False)
        ingest_historical_odds(odds_db, "titan-1", bet365, poll_run_id="run-1")
        ingest_historical_odds(odds_db, "titan-1", crown, poll_run_id="run-1")
        odds_db.commit()
        total = odds_db.execute("SELECT COUNT(*) FROM bronze_ng_odds_snap").fetchone()[0]
        assert total == len(bet365) + len(crown)
