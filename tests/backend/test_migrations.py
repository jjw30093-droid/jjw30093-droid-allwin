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
    "analytics_events", "schema_migrations", "pipeline_alerts",
}

ODDS_TABLES = {
    "dim_team_xref", "dim_team_alias", "dim_match_xref",
    "bronze_ng_odds_snap", "bronze_fm_lineup_snap", "bronze_fm_sideline_snap",
    "silver_odds_moves", "silver_event_moves", "gold_move_cooccurrence",
    "source_health", "schema_migrations", "fixture_sync_ledger", "poll_attempt_log",
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
    # 0010 起 5 个 plan(free/member/daily_picks 在售,pro/premium 下架保留行)
    assert conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 5
    assert {r[0] for r in conn.execute("SELECT id FROM plans WHERE is_active=1")} == {
        "free", "member", "daily_picks"}
    free = {r[0] for r in conn.execute(
        "SELECT entitlement FROM plan_entitlements WHERE plan_id='free'")}
    # 2026-08-11 权限矩阵互换(platform 0012):free 换成 top5 + european_cup,
    # 不再持有 league:lottery(见 backend/queries/leagues.py 同批注释)。
    assert free == {
        "league:epl", "league:top5", "league:european_cup",
        "prediction:top_probability", "odds:summary_delayed",
    }
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


def test_locked_snapshot_editable_but_not_deletable_at_db_layer(tmp_path):
    """migration 0007(2026-08-05):锁定行的 UPDATE 触发器已移除——概率/hash 等
    实质字段现在可以被直接 UPDATE(应用层唯一入口是 edit_snapshot,见
    tests/backend/test_predictions.py::TestEditSnapshot,这里只测 DB 层本身不再
    拦截)。DELETE 触发器不受影响,物理删除依然被拒绝。"""
    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, quiet=True)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    _seed_snapshot(conn, locked=True, official=True)
    conn.commit()
    # 改概率 → 现在允许(DB 层不再兜底,留痕是 edit_snapshot 的应用层职责)
    conn.execute("UPDATE prediction_snapshots SET home_win=0.9, draw=0.05, away_win=0.05 WHERE id='snap1'")
    # 改 hash → 同样允许
    conn.execute("UPDATE prediction_snapshots SET prediction_hash='forged' WHERE id='snap1'")
    row = conn.execute("SELECT home_win, prediction_hash FROM prediction_snapshots WHERE id='snap1'").fetchone()
    assert row == (0.9, "forged")
    # 物理删除 → 依然拒绝(trg_pred_snap_no_delete 未受本次改动影响)
    with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
        conn.execute("DELETE FROM prediction_snapshots WHERE id='snap1'")
    # 撤回状态 → 允许(status 从未受锁定触发器保护)
    conn.execute("UPDATE prediction_snapshots SET status='retracted' WHERE id='snap1'")
    row = conn.execute("SELECT status, home_win FROM prediction_snapshots WHERE id='snap1'").fetchone()
    assert row == ("retracted", 0.9)
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


def test_lottery_entitlement_present_in_all_three_plans_fresh_db(tmp_path):
    """瑞典超接入(0004):league:lottery 必须在全新库的 free/pro/premium 三档都出现,
    不能只加在 free 上指望"继承"(plan_entitlements 是逐档全量枚举,见 0002_seed.sql)。"""
    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, quiet=True)
    conn = sqlite3.connect(db)
    plans = {r[0] for r in conn.execute(
        "SELECT plan_id FROM plan_entitlements WHERE entitlement='league:lottery'")}
    conn.close()
    # 0009/0010 起 member 基线与 daily_picks 物化并集亦含 league:lottery;
    # 0012(权限矩阵互换)把 league:lottery 从 free 移除,free 改持有
    # league:top5 + league:european_cup(见 0012_league_access_swap.sql)。
    assert plans == {"pro", "premium", "member", "daily_picks"}


def test_lottery_entitlement_upgrade_from_pre_0004_db_no_duplicates(tmp_path):
    """升级场景(0004 之前只跑到 0003 的既有库):升级后 league:lottery 正确补齐到
    三档且不重复;既有 plan_entitlements 行不受影响。"""
    staged = tmp_path / "staged_migrations"
    staged.mkdir()
    src = migrate.MIGRATIONS_ROOT / "platform"
    for name in ("0001_init.sql", "0002_seed.sql", "0003_snapshot_kickoff_provenance.sql"):
        shutil.copyfile(src / name, staged / name)
    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, migrations_dir=staged, quiet=True)
    conn = sqlite3.connect(db)
    before = conn.execute("SELECT COUNT(*) FROM plan_entitlements").fetchone()[0]
    assert conn.execute(
        "SELECT COUNT(*) FROM plan_entitlements WHERE entitlement='league:lottery'"
    ).fetchone()[0] == 0
    conn.close()

    # 升级:指向真实(含 0004/0005)的迁移目录
    migrate.apply_all("platform", db_file=db, quiet=True)
    conn = sqlite3.connect(db)
    after = conn.execute("SELECT COUNT(*) FROM plan_entitlements").fetchone()[0]
    # 0004 +3(league:lottery 三档);0009 +13(member 基线);
    # 0010 +16(member 的 reco:track_record 1 行 + daily_picks 物化并集 15 行);
    # 0012 +6/-1(free/pro/premium/member/daily_picks 各 +1 european_cup,
    # free -1 lottery)= 净 +5
    assert after == before + 3 + 13 + 16 + 6 - 1
    plans = {r[0] for r in conn.execute(
        "SELECT plan_id FROM plan_entitlements WHERE entitlement='league:lottery'")}
    assert plans == {"pro", "premium", "member", "daily_picks"}

    # 重复应用同一套完整迁移:不产生重复行(第三次幂等检查)
    migrate.apply_all("platform", db_file=db, quiet=True)
    dup_check = conn.execute("SELECT COUNT(*) FROM plan_entitlements").fetchone()[0]
    assert dup_check == after
    conn.close()


def test_model_league_scope_columns_and_league_id_editable(tmp_path):
    """瑞典超接入(0005):model_versions.applicable_league_ids /
    prediction_snapshots.league_id 列存在。此前(migration 0005)锁定后 league_id
    不可再改写(trg_pred_snap_locked_league_immutable);该触发器已被 migration
    0007(2026-08-05)移除——锁定行现在允许直接编辑,包括 league_id。"""
    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, quiet=True)
    conn = sqlite3.connect(db)
    mv_cols = {r[1] for r in conn.execute("PRAGMA table_info(model_versions)")}
    snap_cols = {r[1] for r in conn.execute("PRAGMA table_info(prediction_snapshots)")}
    assert "applicable_league_ids" in mv_cols
    assert "league_id" in snap_cols

    _seed_snapshot(conn, locked=False, official=False)
    conn.execute("UPDATE prediction_snapshots SET league_id=47 WHERE id='snap1'")
    conn.execute(
        "UPDATE prediction_snapshots SET status='locked', locked_at='2026-07-19T01:00:00Z', is_official=1"
        " WHERE id='snap1'"
    )
    conn.commit()
    conn.execute("UPDATE prediction_snapshots SET league_id=999 WHERE id='snap1'")
    row = conn.execute("SELECT league_id FROM prediction_snapshots WHERE id='snap1'").fetchone()
    assert row == (999,)
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


# ── Schedule-state migration manifest identity closure ─────────────────────


def _write_manifest_file(directory: Path, name: str, sql: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(sql, encoding="utf-8")


@pytest.mark.parametrize(
    "names",
    [
        (),
        ("0002_second.sql",),
        ("0001_first.sql", "0003_third.sql"),
    ],
    ids=("empty-manifest", "missing-first-version", "missing-middle-version"),
)
def test_manifest_gap_is_rejected_before_database_creation(tmp_path, names):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for name in names:
        _write_manifest_file(
            migrations,
            name,
            f"CREATE TABLE t_{name[:4]} (id INTEGER PRIMARY KEY);\n",
        )
    db = tmp_path / "must-not-exist.db"

    with pytest.raises(ValueError, match="连续"):
        migrate.apply_all(
            "core",
            db_file=db,
            migrations_dir=migrations,
            quiet=True,
        )

    assert not db.exists()


def test_duplicate_manifest_version_is_rejected_before_database_creation(tmp_path):
    migrations = tmp_path / "migrations"
    _write_manifest_file(
        migrations,
        "0001_first.sql",
        "CREATE TABLE first_table (id INTEGER PRIMARY KEY);\n",
    )
    _write_manifest_file(
        migrations,
        "0001_second.sql",
        "CREATE TABLE second_table (id INTEGER PRIMARY KEY);\n",
    )
    db = tmp_path / "must-not-exist.db"

    with pytest.raises(ValueError, match="重复版本号"):
        migrate.apply_all(
            "core",
            db_file=db,
            migrations_dir=migrations,
            quiet=True,
        )

    assert not db.exists()


def test_ledger_filename_mismatch_is_rejected_without_database_change(tmp_path):
    migrations = tmp_path / "migrations"
    _write_manifest_file(
        migrations,
        "0001_first.sql",
        "CREATE TABLE first_table (id INTEGER PRIMARY KEY);\n",
    )
    db = tmp_path / "ledger-name.db"
    migrate.apply_all(
        "core",
        db_file=db,
        migrations_dir=migrations,
        quiet=True,
    )
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE schema_migrations SET name='0009_wrong.sql' WHERE version=1"
    )
    conn.commit()
    conn.close()
    before = db.read_bytes()

    with pytest.raises(RuntimeError, match="identity"):
        migrate.apply_all(
            "core",
            db_file=db,
            migrations_dir=migrations,
            quiet=True,
        )

    assert db.read_bytes() == before


def test_ledger_version_missing_from_manifest_is_rejected_without_change(tmp_path):
    migrations = tmp_path / "migrations"
    _write_manifest_file(
        migrations,
        "0001_first.sql",
        "CREATE TABLE first_table (id INTEGER PRIMARY KEY);\n",
    )
    db = tmp_path / "orphan-ledger.db"
    migrate.apply_all(
        "core",
        db_file=db,
        migrations_dir=migrations,
        quiet=True,
    )
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO schema_migrations "
        "(version, name, checksum, applied_at) "
        "VALUES (2, '0002_missing.sql', ?, '2026-07-26T00:00:00Z')",
        ("0" * 64,),
    )
    conn.commit()
    conn.close()
    before = db.read_bytes()

    with pytest.raises(RuntimeError, match="manifest"):
        migrate.apply_all(
            "core",
            db_file=db,
            migrations_dir=migrations,
            quiet=True,
        )

    assert db.read_bytes() == before
