"""ingest_kbisai_odds_points 单测(本轮任务 §8c)——独立于 CLI,直接测写入层的
分类逻辑:UNIQUE 违规→duplicate,CHECK 违规→rejected_constraint,且后者不能
被误判成前者、也不能让整批因为一行数据回滚掉其它合法行。
"""

from backend.db.connections import connect_rw
from backend.ingest.kbisai_odds_snapshots import ingest_kbisai_odds_points


def _row(**overrides) -> dict:
    base = dict(
        provider="kbisai", provider_match_id="4467576", fotmob_match_id=5104970,
        market="ah", source_market="asia", company_id="7", company_name="澳*",
        handicap_line="0.25", odds_home_or_over="0.79", odds_draw=None,
        odds_away_or_under="0.99", closed_flag=0, source_status_id=1,
        going_time="", score="0-0", market_phase="pre_match",
        source_updated_at="2026-08-04T10:00:00Z", observed_at="2026-08-04T10:00:05Z",
        ingested_at="2026-08-04T10:00:05Z", poll_run_id="run-1",
        raw_point_json='{"a":1}', point_hash="hash-a", dup_ordinal=0,
    )
    base.update(overrides)
    return base


def test_insert_then_duplicate_then_distinct_content_same_changetime(data_dir):
    conn = connect_rw("odds")
    try:
        r1 = ingest_kbisai_odds_points(conn, [_row(point_hash="hash-a")])
        assert r1["inserted"] == 1 and r1["duplicate"] == 0 and r1["rejected_constraint"] == 0

        # 幂等重跑:同一条(内容也一样)必须被识别为 duplicate,不是 rejected。
        r2 = ingest_kbisai_odds_points(conn, [_row(point_hash="hash-a")])
        assert r2["inserted"] == 0 and r2["duplicate"] == 1

        # 同一 changeTime,不同赔率内容(point_hash 不同)——必须都保留,不能丢。
        r3 = ingest_kbisai_odds_points(conn, [
            _row(point_hash="hash-b", odds_home_or_over="0.99", handicap_line="0.5")
        ])
        assert r3["inserted"] == 1

        count = conn.execute("SELECT COUNT(*) FROM bronze_kbisai_odds_point").fetchone()[0]
        assert count == 2
    finally:
        conn.close()


def test_byte_identical_duplicate_entries_coexist_via_dup_ordinal(data_dir):
    """来源在同一次响应里给了两条完全字节相同的记录(2016 老比赛上真实观察到过)
    ——两条都要落库,dup_ordinal 区分它们,不能被 UNIQUE 悄悄丢掉一条。"""
    conn = connect_rw("odds")
    try:
        result = ingest_kbisai_odds_points(conn, [
            _row(point_hash="hash-c", dup_ordinal=0),
            _row(point_hash="hash-c", dup_ordinal=1),
        ])
        assert result["inserted"] == 2
        count = conn.execute("SELECT COUNT(*) FROM bronze_kbisai_odds_point").fetchone()[0]
        assert count == 2
    finally:
        conn.close()


def test_check_violation_is_rejected_constraint_not_duplicate_and_does_not_roll_back_batch(data_dir):
    conn = connect_rw("odds")
    try:
        good = _row(point_hash="hash-good")
        bad = _row(point_hash="hash-bad", handicap_line=None)   # ah 市场必须有盘口线
        result = ingest_kbisai_odds_points(conn, [good, bad])

        assert result["inserted"] == 1
        assert result["duplicate"] == 0
        assert result["rejected_constraint"] == 1
        assert len(result["rejected_samples"]) == 1
        assert result["rejected_samples"][0]["index"] == 1

        # 好的那一行必须真的落库了,没有因为坏行而整批回滚。
        row = conn.execute(
            "SELECT point_hash FROM bronze_kbisai_odds_point"
        ).fetchone()
        assert row["point_hash"] == "hash-good"
    finally:
        conn.close()


def test_1x2_market_must_not_have_handicap_line(data_dir):
    conn = connect_rw("odds")
    try:
        bad = _row(
            market="1x2", source_market="eu", point_hash="hash-1x2-bad",
            handicap_line="0.25",   # 1x2 不该有盘口线
            odds_draw="3.6",
        )
        result = ingest_kbisai_odds_points(conn, [bad])
        assert result["rejected_constraint"] == 1
        assert result["inserted"] == 0
    finally:
        conn.close()


def test_append_only_triggers_block_update_and_delete(data_dir):
    import sqlite3

    conn = connect_rw("odds")
    try:
        ingest_kbisai_odds_points(conn, [_row(point_hash="hash-guard")])
        try:
            conn.execute("UPDATE bronze_kbisai_odds_point SET company_name='x'")
            raise AssertionError("expected UPDATE to be blocked")
        except sqlite3.IntegrityError:
            pass
        try:
            conn.execute("DELETE FROM bronze_kbisai_odds_point")
            raise AssertionError("expected DELETE to be blocked")
        except sqlite3.IntegrityError:
            pass
    finally:
        conn.close()
