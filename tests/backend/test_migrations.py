"""P0.2 migration 测试:空库初始化、重复执行幂等、checksum 漂移、失败回滚、锁定触发器。"""

import sqlite3
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from backend.db import migrate

PLATFORM_TABLES = {
    "users", "auth_identities", "auth_sessions", "oauth_states", "device_login_requests",
    "account_links", "roles", "plans", "plan_entitlements", "subscriptions", "redeem_codes",
    "products", "model_versions", "prediction_runs", "prediction_snapshots",
    "prediction_outcomes", "prediction_evaluations", "prediction_manifests",
    "favorites", "content_drafts", "export_jobs", "job_runs", "audit_logs",
    "analytics_events", "schema_migrations",
}

ODDS_TABLES = {
    "dim_team_xref", "dim_team_alias", "dim_match_xref",
    "bronze_ng_odds_snap", "bronze_fm_lineup_snap", "bronze_fm_sideline_snap",
    "silver_odds_moves", "silver_event_moves", "gold_move_cooccurrence",
    "source_health", "schema_migrations",
}


def _tables(db_file):
    conn = sqlite3.connect(db_file)
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    finally:
        conn.close()


def test_platform_fresh_init(tmp_path):
    db = tmp_path / "platform.db"
    applied = migrate.apply_all("platform", db_file=db, quiet=True)
    assert applied >= 2
    assert PLATFORM_TABLES <= _tables(db)
    conn = sqlite3.connect(db)
    # 种子数据齐备
    assert conn.execute("SELECT COUNT(*) FROM roles").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 3
    free = {r[0] for r in conn.execute(
        "SELECT entitlement FROM plan_entitlements WHERE plan_id='free'")}
    assert free == {"league:epl", "prediction:top_probability", "odds:summary_delayed"}
    pro = {r[0] for r in conn.execute(
        "SELECT entitlement FROM plan_entitlements WHERE plan_id='pro'")}
    assert "prediction:full_wdl" in pro and free <= pro  # pro ⊇ free
    premium = {r[0] for r in conn.execute(
        "SELECT entitlement FROM plan_entitlements WHERE plan_id='premium'")}
    assert pro <= premium and "odds:raw" in premium
    assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] >= 4
    conn.close()


def test_odds_fresh_init(tmp_path):
    db = tmp_path / "odds.db"
    migrate.apply_all("odds", db_file=db, quiet=True)
    assert ODDS_TABLES <= _tables(db)


def test_second_run_idempotent(tmp_path):
    db = tmp_path / "platform.db"
    first = migrate.apply_all("platform", db_file=db, quiet=True)
    assert first >= 2
    second = migrate.apply_all("platform", db_file=db, quiet=True)
    assert second == 0
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    conn.close()
    assert n == first


def test_checksum_drift_detected(tmp_path):
    src = migrate.MIGRATIONS_ROOT / "platform"
    mdir = tmp_path / "migrations"
    shutil.copytree(src, mdir)
    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, migrations_dir=mdir, quiet=True)
    # 篡改已应用迁移
    f = mdir / "0002_seed.sql"
    f.write_text(f.read_text() + "\n-- tampered\n")
    with pytest.raises(RuntimeError, match="checksum"):
        migrate.apply_all("platform", db_file=db, migrations_dir=mdir, quiet=True)


def test_status_reports_checksum_drift_without_applying(tmp_path):
    """status() 必须能在不调用 apply_all() 的情况下发现漂移(restore_verify/
    ops_check 只想"检查",不想真的对被检查的库副本执行一次迁移)。"""
    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, quiet=True)
    st = migrate.status("platform", db_file=db)
    assert st["checksum_drift"] == []
    # 直接改写 schema_migrations 里的 checksum,模拟"记录的校验和与当前文件不符"
    conn = sqlite3.connect(db)
    conn.execute("UPDATE schema_migrations SET checksum='tampered' WHERE version=1")
    conn.commit()
    conn.close()
    st2 = migrate.status("platform", db_file=db)
    assert st2["checksum_drift"], "篡改 schema_migrations.checksum 后 status() 必须报告漂移"


def test_failed_migration_rolls_back(tmp_path):
    mdir = tmp_path / "migrations"
    mdir.mkdir()
    (mdir / "0001_good.sql").write_text("CREATE TABLE t1 (id INTEGER PRIMARY KEY);\n")
    (mdir / "0002_bad.sql").write_text(
        "CREATE TABLE t2 (id INTEGER PRIMARY KEY);\n"
        "INSERT INTO no_such_table VALUES (1);\n"
    )
    db = tmp_path / "x.db"
    with pytest.raises(sqlite3.OperationalError):
        migrate.apply_all("platform", db_file=db, migrations_dir=mdir, quiet=True)
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations")]
    conn.close()
    assert "t1" in tables
    assert "t2" not in tables          # 失败迁移的半成品被回滚
    assert versions == [1]             # 只记录了成功的 0001


def test_upgrade_on_copy_of_existing_db(tmp_path):
    """模拟"临时副本升级":先建库,复制一份,再对副本重跑,应幂等无损。"""
    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, quiet=True)
    copy = tmp_path / "platform_copy.db"
    shutil.copyfile(db, copy)
    assert migrate.apply_all("platform", db_file=copy, quiet=True) == 0
    conn = sqlite3.connect(copy)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def _seed_snapshot(conn, locked=False, official=False):
    conn.execute(
        "INSERT OR IGNORE INTO model_versions (id, algorithm, created_at)"
        " VALUES ('m1', 'dixon-coles', '2026-07-19T00:00:00Z')")
    conn.execute(
        """INSERT INTO prediction_snapshots
           (id, match_id, model_version_id, generated_at, locked_at, prediction_hash,
            home_win, draw, away_win, status, is_official, created_at)
           VALUES ('snap1', 1001, 'm1', '2026-07-19T00:00:00Z', ?, 'h',
                   0.5, 0.3, 0.2, ?, ?, '2026-07-19T00:00:00Z')""",
        ("2026-07-19T01:00:00Z" if locked else None,
         "locked" if locked else "draft",
         1 if official else 0))


def test_locked_snapshot_immutable_at_db_layer(tmp_path):
    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, quiet=True)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    _seed_snapshot(conn, locked=True, official=True)
    conn.commit()
    # 改概率 → 拒绝
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE prediction_snapshots SET home_win=0.9, draw=0.05, away_win=0.05 WHERE id='snap1'")
    # 改 hash → 拒绝
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE prediction_snapshots SET prediction_hash='forged' WHERE id='snap1'")
    # 物理删除 → 拒绝
    with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
        conn.execute("DELETE FROM prediction_snapshots WHERE id='snap1'")
    # 撤回状态 → 允许(status 不在锁定列清单)
    conn.execute("UPDATE prediction_snapshots SET status='retracted' WHERE id='snap1'")
    row = conn.execute("SELECT status, home_win FROM prediction_snapshots WHERE id='snap1'").fetchone()
    assert row == ("retracted", 0.5)
    conn.close()


def test_unlocked_draft_is_editable(tmp_path):
    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, quiet=True)
    conn = sqlite3.connect(db)
    _seed_snapshot(conn, locked=False, official=False)
    conn.execute("UPDATE prediction_snapshots SET home_win=0.4, draw=0.35, away_win=0.25 WHERE id='snap1'")
    conn.execute("DELETE FROM prediction_snapshots WHERE id='snap1'")
    conn.commit()
    conn.close()


def test_probability_sum_check(tmp_path):
    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, quiet=True)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO model_versions (id, algorithm, created_at) VALUES ('m1','dc','2026-07-19T00:00:00Z')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO prediction_snapshots
               (id, match_id, model_version_id, generated_at, prediction_hash,
                home_win, draw, away_win, created_at)
               VALUES ('bad', 1, 'm1', '2026-07-19T00:00:00Z', 'h', 0.5, 0.3, 0.5, '2026-07-19T00:00:00Z')""")
    conn.close()


def test_cli_all_with_data_dir(tmp_path):
    """CLI 形态:python -m backend.db.migrate --all --data-dir <tmp> 三库全建。"""
    proc = subprocess.run(
        [sys.executable, "-m", "backend.db.migrate", "--all", "--data-dir", str(tmp_path)],
        capture_output=True, text=True, cwd=str(migrate.PROJECT_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "platform.db").exists()
    assert (tmp_path / "odds.db").exists()
    assert (tmp_path / "allwin.db").exists()  # core:仅 schema_migrations,现有库不受影响
