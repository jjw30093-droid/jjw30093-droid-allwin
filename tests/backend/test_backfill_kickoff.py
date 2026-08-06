"""backfill_kickoff_from_fotmob 的离线测试。

fixture 是磁盘上真实 FotMob leagues 响应(PL 2024/2025)的 10 场子集——
原始响应实证了该端点对已完赛比赛返回精确 utcTime(380/380,且与
dim_match.Date 零日期不一致)。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from backend.cli.backfill_kickoff_from_fotmob import (
    apply_league_payload,
    extract_utc_times,
    list_partitions,
)

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "fotmob" / "leagues-epl-2024-2025-subset.json"


@pytest.fixture()
def payload() -> dict:
    return json.loads(_FIXTURE.read_text())


@pytest.fixture()
def core_db(payload) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE dim_match ("Match_ID" INTEGER PRIMARY KEY, "Season" TEXT,
           "League_ID" INTEGER, "Date" TEXT, "status" TEXT,
           kickoff_at_utc TEXT, kickoff_precision TEXT, kickoff_source TEXT)"""
    )
    for m in payload["fixtures"]["allMatches"]:
        conn.execute(
            "INSERT INTO dim_match VALUES (?,?,?,?,?,?,?,?)",
            (int(m["id"]), "2024/2025", 47, m["status"]["utcTime"][:10],
             "Finish", None, "date_only", None),
        )
    conn.commit()
    return conn


def test_extract_utc_times_real_fixture(payload) -> None:
    utc = extract_utc_times(payload)
    assert len(utc) == 10
    assert utc[4506263] == "2024-08-16T19:00:00Z"    # Man United vs Fulham,真实开球时刻


def test_extract_skips_missing_or_bad_utc() -> None:
    p = {"fixtures": {"allMatches": [
        {"id": "1", "status": {"utcTime": None}},
        {"id": "2", "status": {}},
        {"id": "3", "status": {"utcTime": "2024-08-16"}},          # 只有日期 → 不算精确
        {"id": "4", "status": {"utcTime": "2024-08-16T19:00:00Z"}},
        {"id": "not-int", "status": {"utcTime": "2024-08-16T19:00:00Z"}},
    ]}}
    utc = extract_utc_times(p)
    assert set(utc) == {4}


def test_apply_updates_null_rows_with_provenance(core_db, payload) -> None:
    rep = apply_league_payload(core_db, payload, 47, "2024/2025")
    assert rep["updated"] == 10
    assert rep["missing_in_payload"] == 0
    assert rep["date_mismatch_review"] == []
    rows = core_db.execute(
        "SELECT kickoff_at_utc, kickoff_precision, kickoff_source FROM dim_match"
    ).fetchall()
    assert all(r[0] and r[1] == "exact" and r[2] == "fotmob:leagues" for r in rows)


def test_apply_never_overwrites_existing_kickoff(core_db, payload) -> None:
    core_db.execute(
        "UPDATE dim_match SET kickoff_at_utc='2024-08-16T18:00:00Z',"
        " kickoff_precision='exact', kickoff_source='fotmob:fixtures' WHERE Match_ID=4506263"
    )
    rep = apply_league_payload(core_db, payload, 47, "2024/2025")
    assert rep["updated"] == 9      # 已有值的行不在目标集合里
    row = core_db.execute(
        "SELECT kickoff_at_utc, kickoff_source FROM dim_match WHERE Match_ID=4506263"
    ).fetchone()
    assert row == ("2024-08-16T18:00:00Z", "fotmob:fixtures")   # 原值原封不动


def test_apply_rejects_date_mismatch_to_review(core_db, payload) -> None:
    # 人为把一行的 Date 挪到 10 天前 → 必须拒绝并进 review,不落库
    core_db.execute("UPDATE dim_match SET Date='2024-08-06' WHERE Match_ID=4506263")
    rep = apply_league_payload(core_db, payload, 47, "2024/2025")
    assert rep["updated"] == 9
    assert len(rep["date_mismatch_review"]) == 1
    assert rep["date_mismatch_review"][0]["match_id"] == 4506263
    row = core_db.execute(
        "SELECT kickoff_at_utc FROM dim_match WHERE Match_ID=4506263").fetchone()
    assert row[0] is None


def test_apply_missing_in_payload_stays_null(core_db, payload) -> None:
    core_db.execute(
        "INSERT INTO dim_match VALUES (999999,'2024/2025',47,'2024-08-20','Finish',NULL,'date_only',NULL)"
    )
    rep = apply_league_payload(core_db, payload, 47, "2024/2025")
    assert rep["missing_in_payload"] == 1
    row = core_db.execute(
        "SELECT kickoff_at_utc, kickoff_precision FROM dim_match WHERE Match_ID=999999").fetchone()
    assert row == (None, "date_only")     # 保持 NULL,不补 00:00


def test_apply_dry_run_writes_nothing(core_db, payload) -> None:
    rep = apply_league_payload(core_db, payload, 47, "2024/2025", dry_run=True)
    assert rep["updated"] == 10
    n = core_db.execute(
        "SELECT COUNT(*) FROM dim_match WHERE kickoff_at_utc IS NOT NULL").fetchone()[0]
    assert n == 0


def test_list_partitions_only_null_finish(core_db) -> None:
    core_db.execute(
        "INSERT INTO dim_match VALUES (888888,'2024/2025',47,'2024-08-20','NotStarted',NULL,'date_only',NULL)"
    )
    parts = list_partitions(core_db)
    assert parts == [{"league_id": 47, "season": "2024/2025", "null_kickoff_rows": 10}]
