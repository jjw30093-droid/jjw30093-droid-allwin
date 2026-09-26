"""match_details 的 general 子树 bronze 留存(2026-09-26,第四批 C)。

覆盖:
- migration 0013 在全新 odds 库与"已应用到 0012"的库上都能应用,且可重复运行;
- ingest_general_snapshot:原样(canonical JSON,语义逐字段一致)落库、hash-diff 去重、
  内容变化才追加新行、时间戳纪律(source_updated_at 恒 NULL);
- extract_general_snapshot:缺 general / 非 dict / 空 dict → None;
- run_snapshot_poll(离线 fixture)真实落一行,重跑同一 payload 不重复;
- 单独计数 general_snapshots_inserted,不污染阵容/伤停的 snapshots_inserted;
- ingest_match 路径的 bronze 写失败只告警、不阻断(_store_general_bronze)。
"""

import json
import os
import sqlite3

import pytest

from backend.cli.poll_fotmob_snapshots import run_snapshot_poll
from backend.db import migrate
from backend.db.connections import connect_rw
from backend.ingest.odds_snapshots import ingest_general_snapshot
from backend.providers.fotmob_snapshots import extract_general_snapshot

FIXTURE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "fixtures", "fotmob", "prematch-5104961.json",
)
MATCH_ID = 5104961


def _payload() -> dict:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _seed_match(conn):
    conn.execute(
        """INSERT INTO dim_match
           (Match_ID, Season, League_ID, Date, Home_Team_ID, Away_Team_ID,
            Home_Team_Name, Away_Team_Name, status, Match_Round,
            kickoff_at_utc, kickoff_precision, kickoff_source)
           VALUES (?, '2026', 59, '2026-08-01', 100, 200, 'Home', 'Away', 'NotStarted', '17',
                   '2026-08-01T14:00:00Z', 'exact', 'fotmob:fixtures')""",
        (MATCH_ID,),
    )
    conn.commit()


def test_migration_0013_fresh_and_repeatable(tmp_path):
    db = tmp_path / "odds.db"
    migrate.apply_all("odds", db_file=db, quiet=True)
    migrate.apply_all("odds", db_file=db, quiet=True)  # 重复运行无副作用
    conn = sqlite3.connect(db)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(bronze_fm_general_snap)")]
        assert cols == [
            "id", "fotmob_match_id", "payload_json", "payload_hash",
            "source_updated_at", "observed_at", "ingested_at", "poll_run_id",
        ]
        idx = [r[1] for r in conn.execute("PRAGMA index_list(bronze_fm_general_snap)")]
        assert "idx_fm_general_match" in idx
        names = [r[0] for r in conn.execute("SELECT name FROM schema_migrations")]
        assert "0013_fm_general_snap.sql" in names
        assert names.count("0013_fm_general_snap.sql") == 1  # 重复运行不重复登记
    finally:
        conn.close()


def test_extract_general_snapshot_edge_cases():
    assert extract_general_snapshot({}) is None
    assert extract_general_snapshot({"general": None}) is None
    assert extract_general_snapshot({"general": []}) is None
    assert extract_general_snapshot({"general": {}}) is None
    g = extract_general_snapshot(_payload())
    assert g is not None and g["matchId"] == "5104961"
    assert g["teamColors"]["lightMode"]["home"] == "#f13c26"


def test_ingest_general_snapshot_original_hash_diff_and_timestamps(tmp_path):
    db = tmp_path / "odds.db"
    migrate.apply_all("odds", db_file=db, quiet=True)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        general = extract_general_snapshot(_payload())
        r1 = ingest_general_snapshot(conn, MATCH_ID, general, "2026-08-01T10:00:00Z", "run-1")
        assert r1 == {"inserted": 1, "skipped": 0}
        # 同内容再来一次 → hash-diff 跳过
        r2 = ingest_general_snapshot(conn, MATCH_ID, dict(general), "2026-08-01T10:05:00Z", "run-2")
        assert r2 == {"inserted": 0, "skipped": 1}
        # 内容变化 → 追加新行
        changed = {**general, "finished": True}
        r3 = ingest_general_snapshot(conn, MATCH_ID, changed, "2026-08-01T16:00:00Z", "run-3")
        assert r3 == {"inserted": 1, "skipped": 0}

        rows = conn.execute("SELECT * FROM bronze_fm_general_snap ORDER BY id").fetchall()
        assert len(rows) == 2
        # 原样:JSON 解析回来与来源 general 逐字段一致(含 teamColors 的文字色)
        assert json.loads(rows[0]["payload_json"]) == general
        assert json.loads(rows[0]["payload_json"])["teamColors"]["fontDarkMode"]["home"].startswith("rgba(")
        # 时间戳纪律:来源不声明更新时间 → NULL;observed_at 是传入的观察时间
        assert rows[0]["source_updated_at"] is None
        assert rows[0]["observed_at"] == "2026-08-01T10:00:00Z"
        assert rows[0]["ingested_at"]
        assert rows[0]["poll_run_id"] == "run-1"
        assert rows[0]["fotmob_match_id"] == MATCH_ID
    finally:
        conn.close()


def test_poll_writes_general_bronze_once_and_counts_separately(data_dir):
    core = connect_rw("core")
    try:
        _seed_match(core)
    finally:
        core.close()
    kwargs = dict(
        now_iso="2026-07-31T00:00:00Z",
        offline_payloads={str(MATCH_ID): _payload()},
        match_ids=[MATCH_ID],
    )
    s1 = run_snapshot_poll(**kwargs)
    assert s1["general_snapshots_inserted"] == 1
    # 阵容/伤停口径不被 general 污染(fixture 是赛前 payload,行数以既有口径为准)
    odds = connect_rw("odds")
    try:
        n = odds.execute("SELECT COUNT(*) FROM bronze_fm_general_snap").fetchone()[0]
        assert n == 1
        lineup_rows = odds.execute("SELECT COUNT(*) FROM bronze_fm_lineup_snap").fetchone()[0]
        sideline_rows = odds.execute("SELECT COUNT(*) FROM bronze_fm_sideline_snap").fetchone()[0]
        assert s1["snapshots_inserted"] == lineup_rows + sideline_rows
    finally:
        odds.close()
    # 同一 payload 重跑 → general 不重复
    s2 = run_snapshot_poll(**kwargs)
    assert s2["general_snapshots_inserted"] == 0
    odds = connect_rw("odds")
    try:
        assert odds.execute("SELECT COUNT(*) FROM bronze_fm_general_snap").fetchone()[0] == 1
    finally:
        odds.close()


def test_store_general_bronze_failure_warns_but_does_not_raise(data_dir, monkeypatch, capsys):
    from backend.ingest import ingest_match as im

    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("backend.ingest.odds_snapshots.ingest_general_snapshot", boom)
    im._store_general_bronze(MATCH_ID, _payload())  # 不抛
    err = capsys.readouterr().err
    assert "bronze_fm_general_snap" in err and "database is locked" in err


def test_store_general_bronze_writes_row(data_dir):
    from backend.ingest import ingest_match as im

    im._store_general_bronze(MATCH_ID, _payload())
    odds = connect_rw("odds")
    try:
        row = odds.execute(
            "SELECT fotmob_match_id, payload_json FROM bronze_fm_general_snap"
        ).fetchone()
        assert row["fotmob_match_id"] == MATCH_ID
        assert json.loads(row["payload_json"])["leagueId"] == 59
    finally:
        odds.close()
    # 缺 general 的 payload:静默跳过,不落空对象
    im._store_general_bronze(999, {"content": {}})
    odds = connect_rw("odds")
    try:
        assert odds.execute("SELECT COUNT(*) FROM bronze_fm_general_snap").fetchone()[0] == 1
    finally:
        odds.close()
